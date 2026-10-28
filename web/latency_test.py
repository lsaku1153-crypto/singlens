"""端到端实时延迟测量 —— 验收指标「< 200 ms」的测量入口。

━━━ 为什么必须单独写一个测量脚本 ━━━

  验收指标写的是「端到端延迟 < 200 ms」，但**这个数从来没有实测过**。
  服务端自己报的 proc_ms 只是**分解项**（收到音频块 → 算出音高），
  真正的端到端还要加上：网络往返 + 前端绘制。

  本脚本通过 WebSocket 按**真实语速**（1× 实时）推流，
  测「某一块音频发出」到「包含这一块的音高帧回来」的时间差。
  这才是用户实际感受到的延迟。

━━━ 测量方法 ━━━

  对每个音频块：
      t0 = 发出该块
      等待直到收到 t_ms ≥ 该块结束时刻 的 pitch 帧
      延迟 = 现在 − t0

  这样测到的是「声音进麦克风 → 屏幕上出现对应的音高」的完整链路。

运行（先把 server 起起来）：
    .venv\\Scripts\\python.exe web\\server.py
    # 另开一个终端
    .venv\\Scripts\\python.exe web\\latency_test.py
    .venv\\Scripts\\python.exe web\\latency_test.py --duration 20 --rate 1.0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time

import numpy as np

try:
    import websockets
except ImportError:
    sys.exit("需要 websockets：.venv\\Scripts\\pip.exe install websockets")

try:
    import urllib.request
except ImportError:
    pass

SR = 22050
BASE = "http://127.0.0.1:8000"
WS_BASE = "ws://127.0.0.1:8000"


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read())


def synth_voice(duration_s: float, sr: int = SR, f0: float = 261.6) -> np.ndarray:
    """合成一段"像人声"的信号：有颤音、有力度起伏、有换气。

    为什么不用正弦：正弦太容易，YIN 几乎不耗时；
    带颤音和谐波的信号更接近真实负载，测出的延迟才有意义。
    """
    n = int(duration_s * sr)
    t = np.arange(n) / sr
    # 4 个乐句，每句之间换气
    phrase = 2.6
    vib = 1.0 + 0.012 * np.sin(2 * np.pi * 5.4 * t)
    env = np.zeros(n)
    for k in range(int(duration_s / phrase) + 1):
        a = int(k * phrase * sr)
        b = min(n, int((k * phrase + phrase - 0.35) * sr))
        if a >= n or b <= a:
            continue
        # 句内渐强：从 0.45 渐强到 1.0（这样力度指标才有起伏）
        env[a:b] = np.linspace(0.45, 1.0, b - a)
    # 音高按小星星走
    melody = [60, 60, 67, 67, 69, 69, 67]
    inst = np.zeros(n)
    step = int(0.45 * sr)
    for i in range(0, n, step):
        m = melody[(i // step) % len(melody)]
        inst[i:i + step] = 440.0 * 2 ** ((m - 69) / 12)
    inst = inst * vib
    ph = 2 * np.pi * np.cumsum(inst) / sr
    w = np.sin(ph) + 0.45 * np.sin(2 * ph) + 0.2 * np.sin(3 * ph)
    y = w * env * 0.35
    return np.clip(y, -1, 1)


async def measure(duration_s: float, rate: float, chunk_ms: float, mode: str,
                  verbose: bool):
    print("=" * 76)
    print("端到端实时延迟测量")
    print("=" * 76)
    print(f"  服务端      {BASE}")
    print(f"  目标        端到端 < 200 ms（设计文档 §8 验收指标）")
    print(f"  音频块      {chunk_ms:.0f} ms × {rate:g}× 速度")
    print(f"  总时长      {duration_s:g} s")
    print()

    # ① 开会话
    try:
        s = _post("/api/session", {"mode": mode, "reference_id": "REF_TWINKLE_1",
                                   "singer_id": "latency_probe"})["data"]
    except Exception as e:  # noqa: BLE001
        sys.exit(f"连不上服务端：{e}\n先把 server 起起来："
                 f".venv\\Scripts\\python.exe web\\server.py")
    uid = s["session_uid"]
    print(f"  会话        {uid}（模式 {s['mode']}）")

    y = synth_voice(duration_s)
    audio = (y * 32767).astype(np.int16)
    chunk = int(SR * chunk_ms / 1000)
    n_chunks = len(audio) // chunk

    lat_rtt: list[float] = []
    lat_srv: list[float] = []
    pitch_frames = 0
    metrics_frames = 0
    recv_task = None
    recv_q: asyncio.Queue = asyncio.Queue()

    async def receiver(ws):
        nonlocal pitch_frames, metrics_frames
        try:
            async for raw in ws:
                m = json.loads(raw)
                if m.get("type") == "pitch":
                    pitch_frames += 1
                elif m.get("type") == "metrics":
                    metrics_frames += 1
                await recv_q.put(m)
        except Exception:  # noqa: BLE001
            pass

    url = f"{WS_BASE}/api/session/{uid}/stream"
    async with websockets.connect(url, max_size=None) as ws:
        # ★ 发送与匹配必须【解耦】。
        #   第一版是「发一块 → 阻塞等这一块对应的帧」，结果死锁：
        #   服务端每 50ms 才发一帧，46ms 的块隔一块才触发一帧；
        #   没触发的那块会一直等到超时 —— 而它不继续发音频，
        #   服务端就永远凑不够 50ms，那一帧永远不会来。
        #   实测 10 秒音频推了 328 秒。
        #   正确做法：发送按节拍独立跑，收帧在另一个任务里做匹配。
        pending: list[tuple[float, float]] = []   # [(end_ms, t_send)]

        async def receiver():
            nonlocal pitch_frames, metrics_frames
            try:
                async for raw in ws:
                    m = json.loads(raw)
                    t = m.get("type")
                    if t == "pitch":
                        pitch_frames += 1
                        tms = m["data"]["t_ms"]
                        # 回填所有「已被这一帧覆盖」的待匹配块
                        while pending and pending[0][0] <= tms + 1:
                            end_ms, t_send = pending.pop(0)
                            lat_rtt.append((time.perf_counter() - t_send) * 1000.0)
                            if "proc_ms" in m["data"]:
                                lat_srv.append(m["data"]["proc_ms"])
                        # pitch 帧里的 f0 也给别的测试用
                        await recv_q.put(m)
                    elif t == "metrics":
                        metrics_frames += 1
                        await recv_q.put(m)
                    elif t == "error":
                        await recv_q.put(m)
            except Exception:  # noqa: BLE001
                pass

        recv_task = asyncio.create_task(receiver())
        await ws.send(json.dumps({"type": "start", "session_uid": uid,
                                  "sample_rate": SR}))
        t_start = time.perf_counter()

        for i in range(n_chunks):
            buf = audio[i * chunk:(i + 1) * chunk]
            end_ms = (i + 1) * chunk / SR * 1000.0
            pending.append((end_ms, time.perf_counter()))
            await ws.send(buf.tobytes())
            # 按目标速度推流（1.0 = 真实语速）—— 绝不在这里等回帧
            target = t_start + (i + 1) * (chunk_ms / 1000.0) / rate
            sleep = target - time.perf_counter()
            if sleep > 0:
                await asyncio.sleep(sleep)

            # 顺便看看有没有 error 帧（第一版把错误帧静默丢了，排查了很久）
            while not recv_q.empty():
                m = recv_q.get_nowait()
                if m.get("type") == "error":
                    print(f"  ⚠️ 服务端报错：{m['data']}")

            if verbose and (i + 1) % 40 == 0:
                p50 = statistics.median(lat_rtt[-40:]) if lat_rtt else float("nan")
                print(f"    块 {i+1:>4}/{n_chunks}  近 40 块延迟中位 {p50:>6.1f} ms")

        # 尾包：给服务端一点时间把最后几块回完
        tail_deadline = time.perf_counter() + 1.0
        while pending and time.perf_counter() < tail_deadline:
            await asyncio.sleep(0.05)

        await ws.send(json.dumps({"type": "stop"}))
        await asyncio.sleep(0.3)
        recv_task.cancel()

    total_s = time.perf_counter() - t_start
    print(f"\n  推流完成：{n_chunks} 块 / {total_s:.1f}s")
    print(f"  收到 pitch 帧 {pitch_frames} 个、metrics 帧 {metrics_frames} 个")
    if pitch_frames:
        print(f"  实测帧率 {pitch_frames/total_s:.1f} Hz（契约要求 ~20 Hz）")
    print()

    if not lat_rtt:
        print("  ❌ 没测到任何往返延迟")
        return None

    a = np.array(lat_rtt)
    b = np.array(lat_srv) if lat_srv else np.array([])
    res = {
        "samples": int(a.size),
        "rtt_ms": {"p50": round(float(np.percentile(a, 50)), 1),
                   "p95": round(float(np.percentile(a, 95)), 1),
                   "max": round(float(a.max()), 1),
                   "mean": round(float(a.mean()), 1)},
        "server_proc_ms": ({"p50": round(float(np.percentile(b, 50)), 1),
                            "p95": round(float(np.percentile(b, 95)), 1)} if b.size else None),
        "pitch_frame_hz": round(pitch_frames / total_s, 1),
    }

    print("  ┌─ 端到端延迟（音频发出 → 对应音高帧回来）─────────────")
    print(f"  │  P50   {res['rtt_ms']['p50']:>7.1f} ms")
    print(f"  │  P95   {res['rtt_ms']['p95']:>7.1f} ms")
    print(f"  │  最大  {res['rtt_ms']['max']:>7.1f} ms")
    print(f"  │  均值  {res['rtt_ms']['mean']:>7.1f} ms")
    print("  ├─ 其中服务端处理 ─────────────────────────────────")
    if res["server_proc_ms"]:
        print(f"  │  P50   {res['server_proc_ms']['p50']:>7.1f} ms")
        print(f"  │  P95   {res['server_proc_ms']['p95']:>7.1f} ms")
    print("  └───────────────────────────────────────────────────")
    print()
    p95 = res["rtt_ms"]["p95"]
    verdict = "✅ 达标" if p95 < 200 else "❌ 未达标"
    print(f"  验收判定（P95 < 200 ms）：{verdict}   （P95 = {p95:.1f} ms）")

    # ② 收尾：精算管线也跑一遍，顺便量精算耗时
    print("\n  顺带触发一次精算管线，量一下耗时…")
    try:
        _post(f"/api/session/{uid}/finish", {})
        t0 = time.perf_counter()
        for _ in range(120):
            st = _get(f"/api/session/{uid}/status")["data"]
            if st["status"] in ("SCORED", "FAILED"):
                break
            time.sleep(0.25)
        dt = time.perf_counter() - t0
        print(f"  精算状态：{st['status']}，耗时 {dt:.1f}s"
              f"（{dt/(len(y)/SR):.3f}× 实时，目标 <0.5×）")
        if st["status"] == "SCORED":
            rep = _get(f"/api/session/{uid}/report")["data"]
            print(f"  总分 {rep['total']} · 音准 {rep['pitch']['score']} · "
                  f"节奏 {rep['rhythm']['score']} · 表现力 {rep['expressiveness']['score']}")
            perf = rep["meta"].get("perf_breakdown_ms", {})
            print(f"  分解：音高 {perf.get('pitch_ms')}ms · 切分 {perf.get('segment_ms')}ms · "
                  f"对齐 {perf.get('align_ms')}ms · 指标 {perf.get('features_ms')}ms")
        elif st["status"] == "FAILED":
            print(f"  ⚠️ {st.get('error')}")
    except Exception as e:  # noqa: BLE001
        print(f"  精算触发失败：{e}")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                       "experiments", "output", "latency_report.json")
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(f"\n  报告 -> {out}")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=15.0, help="推流时长（秒）")
    ap.add_argument("--rate", type=float, default=1.0, help="推流速度（1.0 = 真实语速）")
    ap.add_argument("--chunk-ms", type=float, default=93.0,
                    help="每块音频时长（ms）。93ms ≈ 2048 样本 @22050")
    ap.add_argument("--mode", default="A", choices=["A", "B"])
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    asyncio.run(measure(args.duration, args.rate, args.chunk_ms, args.mode, args.verbose))


if __name__ == "__main__":
    main()

"""API 契约自检：逐个打契约 §3.1 的 12 个端点，核对响应结构与约定。

和 web/selftest.html（前端侧 71 项）互补：那个查的是【数据形状】，
这个查的是【接口行为】—— 状态码、统一响应包、错误码、null 语义。

运行（先把 server 起起来）：
    .venv\\Scripts\\python.exe web\\api_selftest.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

import argparse

# ★ 服务端默认绑【局域网 IP】而不是 127.0.0.1（见 server.py 的安全说明），
#   所以这里不能写死回环地址。用 --base 指定，默认自动探测。
BASE = "http://127.0.0.1:8000"
PASS = FAIL = 0
FAILS: list[str] = []


def ck(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        FAILS.append(f"{name}  {detail}")
        print(f"  ❌ {name}   {detail}")


def call(method, path, body=None, expect=200):
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:  # noqa: BLE001
            return e.code, {}
    except Exception as e:  # noqa: BLE001
        return 0, {"_err": str(e)}


def envelope_ok(j):
    """契约 §3.2：统一响应包 {ok:true,data} / {ok:false,error:{code,message}}"""
    if not isinstance(j, dict) or "ok" not in j:
        return False
    if j["ok"]:
        return "data" in j
    e = j.get("error") or {}
    return "code" in e and "message" in e


def main():
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=None,
                    help="服务端地址。默认自动探测（本机回环或局域网 IP）")
    args = ap.parse_args()
    if args.base:
        BASE = args.base.rstrip("/")
    else:
        # 依次试：回环 → 局域网 IP
        import subprocess, re
        cands = ["http://127.0.0.1:8000"]
        try:
            r = subprocess.run(["ipconfig"], capture_output=True, text=True,
                               encoding="gbk", errors="replace")
            for line in (r.stdout or "").splitlines():
                m = re.search(r"IPv4[^:]*:\s*([0-9.]+)", line)
                if m and not m.group(1).startswith(("127.", "169.254.")):
                    cands.append(f"http://{m.group(1)}:8000")
        except Exception:
            pass
        for c in cands:
            try:
                with urllib.request.urlopen(c + "/api/config", timeout=3) as x:
                    if x.status == 200:
                        BASE = c; break
            except Exception:
                continue
    print(f"  服务端：{BASE}")
    print("=" * 76)
    print("API 契约自检 —— docs/接口契约.md §3")
    print("=" * 76)

    st, j = call("GET", "/api/config")
    if st != 200:
        sys.exit(f"服务端没起来（HTTP {st}）。先跑：.venv\\Scripts\\python.exe web\\server.py")

    # ── ⑫ GET/PUT /api/config ─────────────────────────────
    # ★ 结构在契约 v1.1 §7.5 冻结为【四组】：range / recording / feedback / privacy。
    #   原来是扁平 7 个键（fmin/fmax/rt_window_ms/...），与 UI 设置页的四个板块对不上，
    #   也与 mock 不一致 —— selftest.html?src=api 因此报 FAIL。已按 UI 需要改成分组结构。
    print("\n⑫ GET/PUT /api/config")
    ck("响应包符合契约", envelope_ok(j), str(j)[:80])
    cfg = j["data"]
    for g in ("range", "recording", "feedback", "privacy"):
        ck(f"含配置组 {g}", g in cfg and isinstance(cfg.get(g), dict))
    ck("range 含 lo/hi/pitch_tolerance_cents",
       all(k in cfg.get("range", {}) for k in
           ("lo_midi", "hi_midi", "pitch_tolerance_cents")))
    ck("recording.rt_window_ms = 5000（契约：滚动窗口 5 秒）",
       cfg.get("recording", {}).get("rt_window_ms") == 5000)
    ck("privacy.local_only = True（产品原则：全部本地运行）",
       cfg.get("privacy", {}).get("local_only") is True)
    # PUT 按【组】更新，可以只传某一组里的某几个键
    st2, j2 = call("PUT", "/api/config", {"feedback": {"show_jargon": True}})
    ck("PUT 按组可写", st2 == 200 and j2["ok"] and
       j2["data"]["config"]["feedback"]["show_jargon"] is True)
    call("PUT", "/api/config", {"feedback": {"show_jargon": False}})   # 还原
    # 未知组：**明确拒绝**，而不是静默忽略（2026-09-24 更新断言）。
    #   为什么改：原来是"忽略未知组、返回 200、updated 为空" ——
    #   前端写错一个组名时，用户看到的是"保存成功"，而设置**根本没生效**。
    #   这类"静默成功"和本项目其它几个坑（setText 找不到元素不报错、
    #   mark_bindpoints 重复插入内容不报错）是同一类毛病：
    #   界面一切正常，事情没做。现在返回 400 + 可用的组名，
    #   前端能把原因显示出来。见 server.py 的 PUT /api/config。
    st3, j3 = call("PUT", "/api/config", {"不存在的组": {"x": 1}})
    ck("PUT 未知组被拒绝（不再静默成功）",
       st3 == 400 and not j3["ok"] and "不存在" in j3["error"]["message"])

    # ── ① POST /api/session ───────────────────────────────
    print("\n① POST /api/session")
    st, j = call("POST", "/api/session", {"mode": "A", "reference_id": "REF_TWINKLE_1",
                                          "singer_id": "api_selftest"})
    ck("开课式会话成功", st == 200 and j["ok"], str(j)[:100])
    uid = j["data"]["session_uid"] if j["ok"] else None
    ck("返回 session_uid", bool(uid))
    ck("返回采样率与帧率约定",
       all(k in j["data"] for k in ("sample_rate", "rt_window_ms", "pitch_frame_hz", "metrics_frame_hz")))

    st, j = call("POST", "/api/session", {"mode": "A", "reference_id": "NOT_EXIST"})
    ck("参考曲目不存在 → REF_MISSING（契约错误码）",
       st == 404 and j.get("error", {}).get("code") == "REF_MISSING", str(j)[:100])

    # ── ③ finish（空音频应报 NO_VOICE）────────────────────
    print("\n③ POST /api/session/{uid}/finish")
    st, j = call("POST", f"/api/session/{uid}/finish")
    ck("没收到音频 → NO_VOICE（契约错误码）",
       j.get("error", {}).get("code") == "NO_VOICE", str(j)[:100])

    # ── ④ GET status ──────────────────────────────────────
    print("\n④ GET /api/session/{uid}/status")
    st, j = call("GET", f"/api/session/{uid}/status")
    ck("返回状态", st == 200 and "status" in j["data"])
    ck("状态取值在契约枚举内",
       j["data"]["status"] in ("RECORDING", "ANALYZING", "SCORED", "FAILED"),
       j["data"].get("status"))

    st, j = call("GET", "/api/session/sess_not_exist/status")
    ck("不存在的会话 → 404 + NOT_FOUND", st == 404 and j.get("error", {}).get("code") == "NOT_FOUND")

    # ── ⑤ report（还没分析完）─────────────────────────────
    print("\n⑤ GET /api/session/{uid}/report")
    st, j = call("GET", f"/api/session/{uid}/report")
    ck("报告未就绪 → 409", st == 409, f"HTTP {st}")

    # ── ⑥ compare（没有可对比的）──────────────────────────
    print("\n⑥ GET /api/session/{uid}/compare")
    st, j = call("GET", f"/api/session/{uid}/compare")
    # 两种都可能：没有可对比的会话 → 404 NOT_FOUND；
    #             有会话但报告没算完 → 409 CONFLICT（契约 v1.1 §3.4）
    code = j.get("error", {}).get("code")
    ck("无可对比 → 404 NOT_FOUND 或 409 CONFLICT（语义不混）",
       (st == 404 and code == "NOT_FOUND") or (st == 409 and code == "CONFLICT"),
       f"HTTP {st} / {code}")
    ck("错误码与状态码语义一致（不再 409 配 NOT_FOUND）",
       not (st == 409 and code == "NOT_FOUND"), f"HTTP {st} / {code}")

    # ── ⑦⑧⑨ singer ───────────────────────────────────────
    print("\n⑦⑧⑨ /api/singer/{id}/*")
    for path, key in (("/history", "sessions"), ("/progress", "series"), ("/profile", "profile")):
        st, j = call("GET", f"/api/singer/api_selftest{path}")
        ck(f"{path} 返回统一包且含 {key}", st == 200 and j["ok"] and key in j["data"],
           str(j)[:90])

    # ── ⑩ experiment/summary ──────────────────────────────
    print("\n⑩ GET /api/experiment/summary")
    st, j = call("GET", "/api/experiment/summary")
    d = j.get("data", {})
    ck("返回统一包", st == 200 and j["ok"])
    ck("标明权重是占位值（否则会误导）", d.get("weights_are_placeholder") is True)
    ck("留了三组对比基线的位置", "baselines" in d)

    # ── ⑪ upload ──────────────────────────────────────────
    print("\n⑪ POST /api/upload")
    wav = "data/recordings/sep12_5pm_vocal.wav"
    if os.path.exists(wav):
        bd = uuid.uuid4().hex
        data = open(wav, "rb").read()
        body = (f'--{bd}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
                f'Content-Type: audio/wav\r\n\r\n').encode() + data + f"\r\n--{bd}--\r\n".encode()
        req = urllib.request.Request(
            f"{BASE}/api/upload?singer_id=api_selftest",
            data=body, headers={"Content-Type": f"multipart/form-data; boundary={bd}"},
            method="POST")
        with urllib.request.urlopen(req, timeout=180) as r:
            j = json.loads(r.read())
        ck("上传成功并返回 session_uid", j.get("ok") and "session_uid" in j["data"], str(j)[:90])
        uid2 = j["data"]["session_uid"]

        # 等精算完成，验证 ScoreReport 结构
        t0 = time.perf_counter()
        for _ in range(400):
            _, js = call("GET", f"/api/session/{uid2}/status")
            if js["data"]["status"] in ("SCORED", "FAILED"):
                break
            time.sleep(0.25)
        dt = time.perf_counter() - t0
        # 判定看【相对音频时长的比例】，不是绝对秒数
        # （设计目标：精算 < 0.5× 实时。绝对秒数随录音长度变化，没有意义）
        ratio = dt / (j["data"]["duration_ms"] / 1000.0)
        ck("精算 < 0.5× 实时（设计目标）", ratio < 0.5, f"{dt:.1f}s / {ratio:.3f}×")

        _, jr = call("GET", f"/api/session/{uid2}/report")
        if jr.get("ok"):
            rep = jr["data"]
            print(f"     （{len(rep['segments'])} 音符 · 总分 {rep['total']} · "
                  f"耗时 {dt:.1f}s）")
            for k in ("session_uid", "retry_of_uid", "pitch", "rhythm", "expressiveness",
                      "total", "weight_version", "metrics_excluded", "segments",
                      "problems", "comments", "comment_source", "comments_by_metric",
                      "evidence", "meta"):
                ck(f"ScoreReport.{k} 存在", k in rep)
            # ★ 最关键的一条：null 必须等价于 metrics_excluded
            e, ex = rep["expressiveness"], rep["metrics_excluded"]
            for mk, fld in (("vibrato", "vibrato_rate"),):
                ck(f"{mk} 为 null ⟺ metrics_excluded 含它",
                   (e[fld] is None) == (mk in ex), f"null={e[fld] is None} excl={ex}")
            for k in ex:
                ck(f"被剔除的 {k} 文案写「本次未评估」",
                   "未评估" in str(rep["comments_by_metric"].get(k, {}).get("level", "")))
            ck("problems ≤ 3 条（契约）", len(rep["problems"]) <= 3, str(len(rep["problems"])))
            # ★ evidence 结构在契约 v1.1 §7.5 冻结为三段，每段都带【时间轴】和【单位】。
            #   原来是裸数组（cents_dev / onset_dev_ms / rms），没有 t 和 unit ——
            #   UI 画不出图，且与 mock 不一致（selftest.html?src=api 报 FAIL）。
            ev = rep.get("evidence")
            if ev:
                for k in ("rms_envelope", "vibrato_spectrum", "onset_dev_trend"):
                    ck(f"evidence 含 {k}", k in ev)
                if "rms_envelope" in ev:
                    e = ev["rms_envelope"]
                    ck("rms_envelope 带 t / v / unit",
                       all(x in e for x in ("t", "v", "unit")))
                    ck("rms_envelope 降采样 ≤120 点（契约）", len(e["t"]) <= 120,
                       f"实际 {len(e['t'])} 点")
                if "onset_dev_trend" in ev:
                    e = ev["onset_dev_trend"]
                    ck("onset_dev_trend 带三分量（dev/trend/residual）",
                       all(x in e for x in ("t", "dev", "trend", "residual")))
            else:
                ck("evidence 可为 null（UI 不显示「展开看证据」）", True,
                   "本次未产出 evidence")
            ck("分数保留 1 位小数",
               rep["total"] is None or round(rep["total"], 1) == rep["total"])
    else:
        print(f"  [跳过] 找不到 {wav}")

    # ── 附加：延迟接口 ────────────────────────────────────
    print("\n附加：GET /api/latency")
    st, j = call("GET", "/api/latency")
    ck("返回统一包", st == 200 and j["ok"])
    ck("明确说明它不含网络与前端", "note" in j["data"])

    print("\n" + "=" * 76)
    print(f"结果：{PASS} 通过 / {FAIL} 失败")
    if FAILS:
        print("\n失败项：")
        for f in FAILS:
            print(f"  · {f}")
    else:
        print("✅ 全部通过")
    print("=" * 76)


if __name__ == "__main__":
    main()

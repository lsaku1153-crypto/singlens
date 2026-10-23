"""把一首歌分离成人声 / 伴奏（Demucs v4，CPU）。

★ 为什么单独一个脚本、并且必须用 `.venv_ml` 跑：
  项目主环境 `.venv` **没有 torch**（一直刻意避免引入）；而 Demucs 需要
  torch + 4 个 84MB 的模型。让主服务 import torch 会把启动/内存都拖垮。
  所以和 `render_qixuan.py`（AI 人声）同一个套路：
  主服务只 spawn 这个脚本，读它的 stdout；**主进程不 import torch**。

模型：
  `data/models/demucs/f7e0c4bc-ba3fe64a.th`（htdemucs_ft 四模型之一，
  单模型 CPU 30s 音频约 16s；整包四模型约 60s，质量更好但慢 4 倍）。
  用 `--repo` 指到模型目录；找不到模型时会给出可操作的中文错误。

输入输出都是 WAV（44.1kHz）；主服务负责把任意格式转成 WAV 再调这里。

用法：
    .venv_ml\\Scripts\\python.exe analysis/song_separate.py \
        --input data/songs/_jobs/xxx/source.wav \
        --out-dir data/songs/xxx --start 12 --duration 60
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def _fail(msg: str, code: str = "SEPARATE_FAILED") -> int:
    print(json.dumps({"ok": False, "code": code, "message": msg},
                     ensure_ascii=False), flush=True)
    return 1


def _default_repo() -> Path | None:
    """模型目录优先级：环境变量 → data/models/demucs → 旧缓存。"""
    env = (os.environ.get("SINGLENS_DEMUCS_REPO") or "").strip()
    root = Path(__file__).resolve().parent.parent
    cands = []
    if env:
        cands.append(Path(env))
    cands.append(root / "data" / "models" / "demucs")
    # torch.hub 的缓存目录（文件名就是签名-校验和.th）
    hub = Path(os.path.expanduser("~")) / ".cache" / "torch" / "hub" / "checkpoints"
    cands.append(hub)
    for c in cands:
        if c.is_dir() and any(c.glob("*.th")):
            return c
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--start", type=float, default=0.0,
                    help="从第几秒开始（默认 0）")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="处理多少秒（0 = 到结尾）")
    ap.add_argument("--model", default="f7e0c4bc",
                    help="模型签名；默认单个 htdemucs_ft 模型（CPU 约 0.5× 实时）")
    ap.add_argument("--repo", default="", help="模型目录；默认自动找")
    ap.add_argument("--shifts", type=int, default=0,
                    help="shift 增强次数（0 = 关闭；越大越慢）")
    args = ap.parse_args()

    try:
        import numpy as np
        import soundfile as sf
        import torch
        from demucs.apply import apply_model
        from demucs.pretrained import get_model
    except Exception as e:  # noqa: BLE001
        return _fail(f"这个环境没有 Demucs/torch（{type(e).__name__}: {e}）。"
                     f"请用 .venv_ml 运行它。", "NO_DEMUCS")

    src = Path(args.input)
    if not src.exists():
        return _fail(f"输入文件不存在：{src}", "NO_INPUT")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    repo = Path(args.repo) if args.repo else _default_repo()
    if repo is None:
        return _fail("找不到 Demucs 模型。把 f7e0c4bc-ba3fe64a.th 放到 "
                     "data/models/demucs/，或设置 SINGLENS_DEMUCS_REPO。",
                     "NO_MODEL")
    print(json.dumps({"stage": "load", "repo": str(repo),
                      "model": args.model}, ensure_ascii=False), flush=True)

    t0 = time.time()
    torch.set_num_threads(max(1, (os.cpu_count() or 4) - 2))
    try:
        model = get_model(args.model, repo=repo)
    except Exception as e:  # noqa: BLE001
        return _fail(f"Demucs 模型加载失败（{type(e).__name__}: {e}）。"
                     f"模型目录：{repo}", "MODEL_LOAD")

    # 读入 + 截取。Demucs 要求 44.1k 双声道。
    try:
        y, sr = sf.read(str(src), dtype="float32", always_2d=True)
    except Exception as e:  # noqa: BLE001
        return _fail(f"读不了这个 WAV（{type(e).__name__}: {e}）。", "BAD_AUDIO")
    if y.ndim == 1:
        y = np.stack([y, y], axis=1)
    if y.shape[1] == 1:
        y = np.repeat(y, 2, axis=1)
    if y.shape[1] > 2:
        y = y[:, :2]
    if sr != 44100:
        import librosa
        y = librosa.resample(y.T, orig_sr=sr, target_sr=44100).T
        sr = 44100
    a = max(0, int(round(args.start * sr)))
    b = len(y) if args.duration <= 0 else min(len(y), a + int(round(args.duration * sr)))
    if b - a < sr:                      # 少于 1 秒没有意义
        return _fail("选的这一段太短（不足 1 秒）。", "TOO_SHORT")
    y = y[a:b]
    duration_s = len(y) / sr
    print(json.dumps({"stage": "separate", "duration_s": round(duration_s, 2),
                      "device": "cuda" if torch.cuda.is_available() else "cpu"},
                     ensure_ascii=False), flush=True)

    x = torch.from_numpy(y.T.copy()).unsqueeze(0)
    try:
        with torch.no_grad():
            out = apply_model(model, x, device="cpu", shifts=max(0, args.shifts),
                              split=True, overlap=0.25, progress=False)
    except Exception as e:  # noqa: BLE001
        return _fail(f"分离失败（{type(e).__name__}: {e}）。"
                     f"这一段 {duration_s:.0f} 秒，内存可能不够。", "SEPARATE_FAILED")

    # out: [1, sources, 2, N]
    src_names = list(model.sources)
    idx = {s: i for i, s in enumerate(src_names)}
    try:
        vocals = out[0, idx["vocals"]].numpy().T          # [N,2]
        inst = (out[0, idx["drums"]] + out[0, idx["bass"]]
                + out[0, idx["other"]]).numpy().T
    except KeyError:
        return _fail(f"这个模型没有 vocals 轨（来源：{src_names}）。", "NO_VOCALS")
    if vocals.shape[0] != inst.shape[0]:
        return _fail("分离结果的时长不一致。", "BAD_SHAPE")

    def _norm(z, peak=0.95):
        m = float(np.max(np.abs(z))) or 1.0
        return (z * (peak / m)).astype(np.float32) if m > peak else z.astype(np.float32)

    vocals = _norm(vocals)
    inst = _norm(inst)
    vp = out_dir / "vocals.wav"
    ip = out_dir / "instrumental.wav"
    sf.write(str(vp), vocals, sr, subtype="PCM_16")
    sf.write(str(ip), inst, sr, subtype="PCM_16")

    print(json.dumps({
        "ok": True, "stage": "done",
        "vocals": str(vp), "instrumental": str(ip),
        "duration_s": round(duration_s, 2), "sr": sr,
        "seconds": round(time.time() - t0, 2),
        "sources": src_names,
    }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

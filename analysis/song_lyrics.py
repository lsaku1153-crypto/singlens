"""从分离出的原唱人声里识别歌词（Whisper，行级时间戳）。

★ 为什么单独一个脚本、并且用 `.venv_ml` 跑：
  主服务 `.venv` 没有 torch/ctranslate2；Whisper 依赖重，不能塞进主进程。
  和 `song_separate.py` 同一个套路：主服务只 spawn，读 stdout 的 JSON。

★ 为什么输入是 **vocals.wav** 而不是原曲：
  Demucs 已经把伴奏分走了。直接识别原唱人声比识别混音准得多，
  也不会把间奏的乐器声识别成歌词。

输出（stdout 最后一个 JSON）：
  {"ok": true, "model": "small", "language": "zh",
   "lyrics": [{"start_ms": 0, "end_ms": 3200, "text": "..."}, ...]}

⚠️ 歌词是**自动识别**，会出错（同音字、气声、和声、重复段）。
   所以产品侧要写明"自动识别，可能有错"；错了也不该影响评分。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path


def _fail(msg: str, code: str = "LYRICS_FAILED") -> int:
    print(json.dumps({"ok": False, "code": code, "message": msg},
                     ensure_ascii=False), flush=True)
    return 1


def _clean(text: str) -> str:
    text = re.sub(r"\s+", "", text or "")
    text = text.strip("，。！？；、,.!?;:： ")
    try:
        import zhconv
        text = zhconv.convert(text, "zh-cn")
    except Exception:  # noqa: BLE001
        pass
    return text


def _split_long(text: str, start: int, end: int, max_chars: int):
    """把一个过长的 token/句子按标点或字数切开，时间按字数比例分。"""
    text = _clean(text)
    if not text:
        return []
    chunks = []
    for part in _split_by_punct(text):
        while len(part) > max_chars:
            chunks.append(part[:max_chars])
            part = part[max_chars:]
        if part:
            chunks.append(part)
    if not chunks:
        return []
    # 避免「…去」+「熬」这种一字尾巴：从上一块挪一个字过来。
    for i in range(len(chunks) - 1, 0, -1):
        if len(chunks[i]) < 2 and len(chunks[i - 1]) > 2:
            chunks[i] = chunks[i - 1][-1] + chunks[i]
            chunks[i - 1] = chunks[i - 1][:-1]
    total = sum(len(x) for x in chunks) or 1
    out = []
    t = start
    for ch in chunks:
        d = max(1, int(round((end - start) * len(ch) / total)))
        out.append({"text": ch, "start_ms": t, "end_ms": min(end, t + d)})
        t += d
    return out


def _split_by_punct(text: str):
    """把一段文本按中文标点切成短句（保留标点语义，不保留标点字符）。"""
    parts = re.split(r"(?<=[，。！？；、,.!?;])", text)
    return [p for p in (_clean(x) for x in parts) if p]


def _emit_segment_tokens(seg):
    """优先用词级时间戳；没有就按字符比例分。"""
    text = _clean(getattr(seg, "text", "") or "")
    if not text:
        return []
    words = getattr(seg, "words", None) or []
    if words:
        out = []
        for w in words:
            t = _clean(getattr(w, "word", "") or "")
            if not t:
                continue
            out.append({
                "text": t,
                "start_ms": int(round(float(getattr(w, "start", 0.0)) * 1000)),
                "end_ms": int(round(float(getattr(w, "end", 0.0)) * 1000)),
            })
        if out:
            return out
    # 没有词级时间戳：整段按字符数比例分（对行级高亮足够）
    a = int(round(float(getattr(seg, "start", 0.0)) * 1000))
    b = int(round(float(getattr(seg, "end", 0.0)) * 1000))
    n = max(1, len(text))
    step = max(1, (b - a) // n)
    return [{"text": ch, "start_ms": a + i * step,
             "end_ms": a + (i + 1) * step} for i, ch in enumerate(text)]


def _group_lines(tokens, max_chars: int = 18, line_gap_ms: int = 350):
    """把词/字聚成"一行歌词"：遇到**停顿**、标点或超过长度就断行。

    ★ 2026-10-21：作者反馈「歌词切分不太好，有间隔就得有切分」。
      原来只看标点和字数，把「也曾一起想有个地方睡觉吃 / 饭可怎么去熬」
      这种中间有明显换气的两句粘成一行。词级时间戳里相邻词的空隙
      就是停顿，>= `line_gap_ms` 必须断。
    """
    lines = []
    cur = None
    gap = max(120, int(line_gap_ms))

    def flush():
        nonlocal cur
        if cur is not None:
            txt = _clean(cur["text"])
            if txt:
                lines.append({"start_ms": cur["start_ms"],
                              "end_ms": cur["end_ms"], "text": txt})
        cur = None

    for token in tokens:
        for t in _split_long(token["text"], token["start_ms"],
                             token["end_ms"], max_chars):
            if cur is not None and t["start_ms"] - cur["end_ms"] >= gap:
                flush()
            if cur is not None and len(cur["text"]) + len(t["text"]) > max_chars:
                flush()
            if cur is None:
                cur = {"start_ms": t["start_ms"], "end_ms": t["end_ms"], "text": ""}
            cur["text"] += t["text"]
            cur["end_ms"] = max(cur["end_ms"], t["end_ms"])
            if t["text"][-1:] in "，。！？；、,.!?;":
                flush()
    flush()
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--model", default=os.environ.get("SINGLENS_WHISPER_MODEL", "small"))
    ap.add_argument("--model-dir", default="",
                    help="模型下载/缓存目录；默认 data/models/whisper")
    ap.add_argument("--language", default="zh")
    ap.add_argument("--max-line-chars", type=int, default=12)
    ap.add_argument("--line-gap-ms", type=int, default=350,
                    help="两个词之间超过这个间隔就断行")
    args = ap.parse_args()

    src = Path(args.input)
    if not src.exists():
        return _fail(f"输入不存在：{src}", "NO_INPUT")
    try:
        from faster_whisper import WhisperModel
    except Exception as e:  # noqa: BLE001
        return _fail(f"这个环境没有 faster-whisper（{type(e).__name__}: {e}）。"
                     f"请用 .venv_ml 运行，并安装 faster-whisper。", "NO_ASR")

    root = Path(__file__).resolve().parent.parent
    model_dir = Path(args.model_dir) if args.model_dir else (
        root / "data" / "models" / "whisper")
    model_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    print(json.dumps({"stage": "load", "model": args.model,
                      "model_dir": str(model_dir)}, ensure_ascii=False),
          flush=True)
    # ★ 优先用**本地模型目录**：`data/models/whisper/small/` 里放
    #   config.json / model.bin / tokenizer.json / vocabulary.txt。
    #   这样不需要连 HuggingFace（国内网络经常卡在 xet/CAS 401）。
    local = model_dir / args.model
    model_ref = str(local) if (local / "model.bin").exists() else args.model
    try:
        model = WhisperModel(model_ref, device="cpu", compute_type="int8",
                             download_root=str(model_dir),
                             local_files_only=(model_ref != args.model))
    except Exception as e:  # noqa: BLE001
        return _fail(f"Whisper 模型加载失败（{type(e).__name__}: {e}）。"
                     f"首次使用需要联网下载模型到 {model_dir}。", "MODEL_LOAD")
    print(json.dumps({"stage": "transcribe"}, ensure_ascii=False), flush=True)

    def _run(vad: bool):
        segs, info = model.transcribe(
            str(src), language=(args.language or None), beam_size=5,
            best_of=5, temperature=0.0, condition_on_previous_text=False,
            word_timestamps=True, vad_filter=vad,
            vad_parameters={"min_silence_duration_ms": 500} if vad else None,
            no_speech_threshold=0.6)
        return list(segs), info

    try:
        try:
            segments, info = _run(True)
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"stage": "vad_fallback", "error": str(e)[:160]},
                             ensure_ascii=False), flush=True)
            segments, info = _run(False)
    except Exception as e:  # noqa: BLE001
        return _fail(f"识别失败（{type(e).__name__}: {e}）。", "TRANSCRIBE")

    tokens = []
    for seg in segments:
        tokens.extend(_emit_segment_tokens(seg))
    lines = _group_lines(tokens, max_chars=max(8, args.max_line_chars),
                         line_gap_ms=max(120, args.line_gap_ms))
    # 去掉重叠/空行，限制到合理数量（10 分钟歌词一般 < 120 行）
    clean = []
    for ln in lines[:400]:
        if not ln["text"]:
            continue
        if clean and ln["start_ms"] <= clean[-1]["start_ms"]:
            continue
        clean.append(ln)
    print(json.dumps({
        "ok": True, "model": args.model,
        "language": getattr(info, "language", args.language),
        "duration_s": round(time.time() - t0, 2),
        "line_count": len(clean), "lyrics": clean,
    }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

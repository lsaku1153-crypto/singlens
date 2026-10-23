"""「上传歌曲 → 自动分出人声/伴奏 → 用原唱当参考」的参考构建。

━━━ 这一层为什么存在（2026-10-20）━━━

作者要的是：**上传一首歌，系统分析出来，用户直接跟唱这首歌出评分。**
项目此前试过三条路都失败（§92/§93/§100/§102/§103/§104/§134）：

  ❌ 从混音里直接提旋律（PYIN 的单声源假设不成立）
  ❌ UVR kuielab 分离 + Basic Pitch 转写（泄漏 -14.1 dB；转写错音）
  ❌ 让模型输出符号旋律（GAME small 质量不够）

这一版换了**目标**：不再追求"把歌转成 MIDI"，而是
**把原唱人声分离出来，直接用它当参考**（就像模式 B 的第一遍）。
  · 参考 = 原唱人声的 Note 序列（项目自己的 PYIN + 切分，不经过转写模型）
  · 伴奏 = 分离出的 instrumental（鼓/贝斯/其他），给用户跟唱
  · 评分 = 现有 B 模式两遍制（DTW + 六指标 + KTV），一行没改
这样绕开了"转写符号化"这一步 —— 那一步才是之前所有错误的来源。

关键在于**分离质量**。Demucs v4 htdemucs_ft 单模型实测：
  · 30s 音频 CPU 约 16s；
  · 人声泄漏（把 instrumental 投影到 vocal 上）约 **-28 dB**，
    明显好于旧 UVR kuielab 的 -14 dB；
  · 对 3 首真实素材做"原唱人声 vs 它自己的参考"自评：
    pitch 86.9–91.5、KTV 93–96（链路通，不是恒 0）。

⚠️ 但**不是每首歌都能分离**：纯人声/几乎没有伴奏的素材，Demucs 会把
   人声同时放进 other 轨（实测泄漏 -2.8 dB）。所以本模块**必须给质量分**，
   低质量时不让用户直接信这个分数（`assess_quality` 的 bad 分支）。
"""
from __future__ import annotations

import os
from typing import Optional

import numpy as np

from .notes import note_stats, segment_notes
from .pitch import extract_f0_pyin_auto

SR = 22050
GUIDE_PRE = 2.0          # 与内置曲目/模板引导保持一致：2 秒 4 拍预备
GUIDE_BEAT = 0.5


def _load_mono(path: str, sr: int = SR) -> np.ndarray:
    import librosa
    y, _ = librosa.load(path, sr=sr, mono=True)
    return y.astype(np.float32)


def measure_leak(vocals_path: str, instrumental_path: str,
                 sr: int = SR) -> Optional[dict]:
    """估计伴奏里还残留多少原唱（dB，越负越干净）。

    做法：把 instrumental 投影到 vocal 上（时域最小二乘），
    投影分量的能量相对 instrumental 总能量 = 泄漏量。
    乐器与歌声不相关 ⇒ 投影接近 0；分离没分干净 ⇒ 投影明显大于 0。

    实测：干净的分离 -28 ~ -45 dB；没分开的素材 -2.8 dB。
    ⚠️ 这不是"人耳听感"的绝对量，只用于**门控**（阈值 -20 / -15 dB）。
    """
    try:
        v = _load_mono(vocals_path, sr)
        i = _load_mono(instrumental_path, sr)
    except Exception:  # noqa: BLE001
        return None
    n = min(len(v), len(i))
    if n < sr // 2:
        return None
    v, i = v[:n], i[:n]
    vv = float(np.dot(v, v))
    ii = float(np.dot(i, i))
    if vv <= 1e-12 or ii <= 1e-12:
        return None
    alpha = float(np.dot(i, v) / vv)
    # 泄漏能量 = |alpha|^2 * ||v||^2；再相对 ||i||^2
    leak_db = (20.0 * np.log10(max(abs(alpha), 1e-9))
               + 10.0 * np.log10(vv / ii))
    return {"alpha": round(alpha, 5), "leak_db": round(float(leak_db), 2)}


def extract_reference(vocals_path: str, sr: int = SR) -> dict:
    """原唱人声 → Note 序列 + 质量。

    与 `web/server.py:run_precise` 用**同一条** PYIN + 切分管线，
    所以参考和评分对"什么是一个音"的判断完全一致。
    """
    y = _load_mono(vocals_path, sr)
    if len(y) < sr // 2:
        return {"ok": False, "code": "TOO_SHORT",
                "message": "人声太短（不足 0.5 秒）。"}
    f0, _, _, range_info = extract_f0_pyin_auto(y, sr)
    notes, _, _ = segment_notes(y, sr, f0=f0)
    st = note_stats(notes)
    dur_ms = int(round(len(y) / sr * 1000))
    note_ms = int(sum(max(0, n.end_ms - n.start_ms) for n in notes))
    voiced = float(np.mean(f0 > 0)) if len(f0) else 0.0
    pitches = [float(n.pitch_midi) for n in notes if n.pitch_midi is not None]
    span = (max(pitches) - min(pitches)) if pitches else 0.0
    quality = {
        "notes": len(notes),
        "duration_ms": dur_ms,
        "coverage": round(note_ms / max(1, dur_ms), 3),
        "voiced_ratio": round(voiced, 3),
        "pitch_span_semitones": round(float(span), 2),
        "feasible": bool(st.get("feasible")),
        "reason": st.get("reason") or "",
        "range": range_info,
    }
    return {"ok": True, "notes": notes, "quality": quality,
            "f0": f0, "range": range_info}


def assess_quality(reference: dict, leak: Optional[dict]) -> dict:
    """把技术量翻成产品能用的三档 + 可执行建议。

    三档：
      good —— 可以直接信这个分数；默认允许开始跟唱
      ok   —— 能用，但报告里要写"参考自动提取"
      bad  —— 不建议直接评分；默认禁用"开始跟唱"，要用户显式确认
    """
    q = dict(reference.get("quality") or {})
    leak_db = (leak or {}).get("leak_db")
    q["leak_db"] = leak_db
    flags = []
    notes = int(q.get("notes") or 0)
    coverage = float(q.get("coverage") or 0)
    span = float(q.get("pitch_span_semitones") or 0)
    voiced = float(q.get("voiced_ratio") or 0)

    if leak_db is not None and leak_db > -15:
        flags.append("伴奏里还能听到原唱（分离不干净）")
    elif leak_db is not None and leak_db > -20:
        flags.append("伴奏里有轻微原唱残留")
    if not q.get("feasible"):
        flags.append("人声切分质量不足")
    if notes < 12:
        flags.append(f"只提取到 {notes} 个音（太少）")
    if coverage < 0.15:
        flags.append("人声太稀疏：这一段可能主要是伴奏")
    if span < 4 or span > 32:
        flags.append(f"音域跨度 {span:.0f} 个半音，不像正常旋律")

    bad = (not q.get("feasible") or notes < 12 or coverage < 0.15
           or (leak_db is not None and leak_db > -15)
           or span < 4 or span > 32)
    if bad:
        level = "bad"
    elif ((leak_db is None or leak_db <= -20) and notes >= 20
          and coverage >= 0.25 and 5 <= span <= 30 and voiced >= 0.35):
        level = "good"
    else:
        level = "ok"
    q["level"] = level
    q["flags"] = flags
    q["summary"] = {
        "good": "参考质量好：伴奏干净、旋律完整。",
        "ok": "参考质量一般：能用，但这是从原唱自动提取的，不是人工校对的谱。",
        "bad": "参考质量不足：这段不适合直接评分（详见提示）。",
    }[level]
    return q


def crop_wav(src_path: str, out_path: str, start_ms: int, end_ms: int) -> str:
    """把 44.1k WAV 裁一段（微练习的伴奏/原唱片段）。

    与 `song_separate.py` 输出同一格式（44.1k 双声道 PCM16），
    这样 `make_guide` 和 `/api/template/{tid}/guide.wav` 不用区分来源。
    """
    import soundfile as sf
    y, sr = sf.read(src_path, dtype="float32", always_2d=True)
    a = max(0, int(round(start_ms / 1000.0 * sr)))
    b = min(len(y), int(round(end_ms / 1000.0 * sr)))
    if b - a < 1:
        raise ValueError("裁剪区间为空")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    sf.write(out_path, y[a:b], sr, subtype="PCM_16")
    return out_path


def make_guide(instrumental_path: str, out_path: str,
               pre: float = GUIDE_PRE, beat: float = GUIDE_BEAT,
               vocal_path: Optional[str] = None) -> str:
    """2 秒 4 拍预备 + 伴奏（或原唱+伴奏）→ 练习用引导音轨。

    和内置曲目/模板的 guide 保持同一形状（前 2 秒是节拍），
    前端的倒数提示、`guideLeadMs=2000`、进度条都不用改。
    ★ 2026-10-20：`vocal_path` 非空时混入原唱 —— 用户说的「跟原曲」
      是**原唱包括伴奏一起放**，不是只放干声。
    """
    import soundfile as sf
    y, sr = sf.read(instrumental_path, dtype="float32", always_2d=True)
    if y.ndim == 1:
        y = np.stack([y, y], axis=1)
    if y.shape[1] == 1:
        y = np.repeat(y, 2, axis=1)
    if vocal_path and os.path.exists(vocal_path):
        v, vsr = sf.read(vocal_path, dtype="float32", always_2d=True)
        if v.ndim == 1:
            v = np.stack([v, v], axis=1)
        if v.shape[1] == 1:
            v = np.repeat(v, 2, axis=1)
        if vsr != sr:
            # 两条音轨都是 song_separate 出的 44.1k；不一致时按较短的裁，
            # 不做重采样（重采样会引入新的相位/伪影）。
            n = min(len(y), len(v))
            y, v = y[:n], v[:n]
        else:
            n = min(len(y), len(v))
            y, v = y[:n], v[:n]
        # 原唱略高于伴奏，保证歌词清楚；峰值最后统一限幅。
        y = y * 0.82 + v * 1.0
    n_pre = int(round(pre * sr))
    y = y / (max(1.0, float(np.max(np.abs(y)))) * 1.02)
    out = np.zeros((n_pre + len(y) + int(0.25 * sr), 2), dtype=np.float32)
    # 4 下短促的"哒"（1000Hz，60ms，指数衰减）——与 _synth_guide 一致
    for k in range(4):
        i0 = int(round(k * beat * sr))
        n = int(0.06 * sr)
        tt = np.arange(n) / sr
        env = np.exp(-tt * 55.0)
        out[i0:i0 + n, :] += (0.28 * np.sin(2 * np.pi * 1000.0 * tt) * env)[:, None]
    out[n_pre:n_pre + len(y)] += y
    peak = float(np.max(np.abs(out))) or 1.0
    if peak > 0.95:
        out *= 0.95 / peak
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    sf.write(out_path, out, sr, subtype="PCM_16")
    return out_path

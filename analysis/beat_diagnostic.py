"""节拍级**诊断**（不参与总分）。

句级回答“这一句有没有进晚/唱长”；节拍级回答“这一句里面的每一拍
有没有踩到、是整段偏前/偏后、还是每拍忽快忽慢”。

设计约束
--------
· 参考是原唱人声，很多拍没有歌词；只诊断**参考有演唱活动**的拍，
  不拿伴奏里的空拍惩罚用户。
· 先算整遍相位（平均偏早/偏晚），再算“扣掉相位后的命中率/稳定度”；
  否则一个固定的 1.3 秒延迟会把所有拍判成没踩中，看起来像节奏差。
· 只输出诊断，不改 `rhythm` / `total`。等真实素材验证后再决定是否计分。

节拍网格来自模板的 **backing 伴奏**（librosa beat_track）。模板没有
伴奏时返回 None，不硬造拍点。
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Iterable, Optional

import numpy as np

BEAT_TOLERANCE_MS = 120.0
BEAT_ACTIVE_WINDOW_MS = 250.0
BEAT_SEARCH_MS = 300.0


def _get(obj: Any, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _abs(path: str) -> str:
    if not path:
        return ""
    return path if os.path.isabs(path) else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), path)


@lru_cache(maxsize=8)
def _grid_cached(path: str, mtime_ns: int, sr: int, offset_ms: int):
    import librosa
    y, _sr = librosa.load(path, sr=sr, mono=True)
    if y is None or len(y) == 0:
        return None
    tempo, frames = librosa.beat.beat_track(
        y=y, sr=sr, hop_length=512, units="frames")
    times = librosa.frames_to_time(frames, sr=sr, hop_length=512)
    if times is None or len(times) < 4:
        return None
    bpm = float(np.atleast_1d(tempo)[0]) if tempo is not None else 0.0
    # beat_track 在慢歌上经常给出 180+ BPM（实际是八分音符）。
    # 统一到 60–140 的“拍”范围；不是改歌，只是把网格折回一拍。
    if bpm > 140:
        times = times[::2]
        bpm = bpm / 2.0
    elif bpm and bpm < 55:
        bpm = bpm * 2.0
    times = np.asarray(times, dtype=float) + float(offset_ms) / 1000.0
    return tuple(float(x) for x in times), float(bpm)


def beat_grid_from_audio(path: str, sr: int = 22050,
                         offset_ms: int = 2000) -> Optional[dict]:
    """从伴奏 WAV 取拍点；失败/没有伴奏返回 None。"""
    p = _abs(path)
    if not p or not os.path.exists(p):
        return None
    try:
        mtime = os.stat(p).st_mtime_ns
        got = _grid_cached(p, mtime, int(sr), int(offset_ms))
    except Exception:  # noqa: BLE001
        return None
    if not got:
        return None
    beats, bpm = got
    # 统一用毫秒，和 notes / report 的时间轴一致。
    return {"beats": [round(float(x) * 1000.0, 3) for x in beats],
            "tempo_bpm": round(float(bpm), 2),
            "source": "backing", "offset_ms": int(offset_ms)}


def _near_start(note, t: float, win: float, offset_ms: float = 0.0) -> Optional[float]:
    s = _get(note, "start_ms")
    if s is None:
        return None
    d = (float(s) + float(offset_ms)) - t
    return d if abs(d) <= win else None


def _spans(note, t: float, offset_ms: float = 0.0) -> bool:
    s = _get(note, "start_ms")
    e = _get(note, "end_ms")
    if s is None:
        return False
    if e is None:
        e = s
    return (float(s) + float(offset_ms)) <= t <= (float(e) + float(offset_ms))


def diagnose_beats(user_notes: Iterable, ref_notes: Iterable,
                   beats: Iterable[float],
                   tolerance_ms: float = BEAT_TOLERANCE_MS,
                   active_window_ms: float = BEAT_ACTIVE_WINDOW_MS,
                   max_worst_bars: int = 3,
                   ref_offset_ms: float = 0.0) -> Optional[dict]:
    """返回节拍诊断；没有可用拍点/参考活动时返回 None。

    `ref_offset_ms`：引导音开头有 2 秒预备，beat grid 和用户录音都含
    这 2 秒，而参考音符表是原始歌曲时间轴 → 比较参考活动时要加回来。
    """
    bs = sorted(float(x) for x in (beats or []))
    un = list(user_notes or [])
    rn = list(ref_notes or [])
    if len(bs) < 4 or not un or not rn:
        return None

    rows = []
    for i, t in enumerate(bs):
        # 参考有演唱的拍才算“该踩”
        active = False
        for n in rn:
            if (_spans(n, t, ref_offset_ms)
                    or _near_start(n, t, active_window_ms, ref_offset_ms) is not None):
                active = True
                break
        if not active:
            continue

        off = None
        for n in un:
            d = _near_start(n, t, BEAT_SEARCH_MS)
            if d is not None and (off is None or abs(d) < abs(off)):
                off = d
        # 长音覆盖这一拍：算“有唱”，但不能把 0 当成相位 ——
        # 否则几十个长音会把整遍相位拉到 0，看起来像用户没有延迟。
        span = False
        if off is None:
            for n in un:
                if _spans(n, t):
                    span = True
                    break
        rows.append({"i": i, "t": t, "off": off, "span": span})

    if len(rows) < 4:
        return None
    raw = [r["off"] for r in rows if r["off"] is not None]
    if len(raw) < 3:
        return None
    phase = float(np.median(raw))
    centered = [float(r["off"]) - phase for r in rows if r["off"] is not None]
    hits = sum(1 for r in rows
               if r["span"] or (r["off"] is not None
                                and abs(float(r["off"]) - phase) <= tolerance_ms))
    raw_hits = sum(1 for r in rows
                   if r["span"] or (r["off"] is not None
                                    and abs(float(r["off"])) <= tolerance_ms))
    stab = float(np.std(centered)) if centered else None

    # 4 拍一小节；只排“至少 3 个有效拍”的小节，避免把空拍排成最差。
    bars = {}
    for r in rows:
        bars.setdefault(r["i"] // 4, []).append(r)
    worst = []
    for b, rs in bars.items():
        if len(rs) < 3:
            continue
        valid = [x for x in rs if x["off"] is not None]
        cc = [float(x["off"]) - phase for x in valid]
        h = sum(1 for x in rs
                if x["span"] or (x["off"] is not None
                                 and abs(float(x["off"]) - phase) <= tolerance_ms))
        worst.append({
            "bar": b + 1,
            "start_ms": round(min(x["t"] for x in rs), 1),
            "end_ms": round(max(x["t"] for x in rs), 1),
            "active_beats": len(rs),
            "hit_beats": h,
            "hit_rate": round(h / len(rs) * 100.0, 1),
            "mean_offset_ms": (round(float(np.mean(cc)), 1) if cc else None),
            "stability_ms": (round(float(np.std(cc)), 1) if cc else None),
        })
    worst.sort(key=lambda x: (x["hit_rate"], -(x["stability_ms"] or 0.0)))
    worst = worst[:max_worst_bars]

    return {
        "scored": False,
        "method": "beat",
        "source": "backing",
        "tolerance_ms": float(tolerance_ms),
        "active_beats": len(rows),
        "hit_beats": hits,
        "hit_rate": round(hits / len(rows) * 100.0, 1),
        "raw_hit_rate": round(raw_hits / len(rows) * 100.0, 1),
        "phase_ms": round(phase, 1),
        "stability_ms": (round(stab, 1) if stab is not None else None),
        "beats_total": len(bs),
        "worst_bars": worst,
        "note": "节拍诊断不参与总分；只统计参考有演唱活动的拍。",
    }

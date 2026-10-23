"""句级节奏评分：把整首歌切成歌词句/乐句，再比较每句的进唱、
句长和句内起伏。

为什么需要它
------------
逐音节奏（每个音符对参考 onset）对切分很敏感：用户唱出 506 个音、
参考 355 个音时，DTW 只要错配十几个音，后面就会累计出几百毫秒的
假抖动。用户听到的是“这一句有没有跟上”，不是“第 317 个音差 285ms”。

这里不替换逐音诊断，只给报告一个**句级口径**：
· 句首偏移：这句比期望时间早/晚多少
· 句长比：这句唱得比参考快/慢多少（已扣掉整遍速度）
· 句内残差：句内各音相对这句自己的速度线，是否还在抖

期望时间用全曲稳健拟合（user_start ≈ a·ref_start + b），
所以整遍慢 1% 不会被重复算进每一句。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import numpy as np

# 阈值不是“拍脑袋的宽松档”，而是先让句级能区分“整句拖/抢”和
# “句内忽快忽慢”。后续用真实素材再校准。
START_FULL_MS = 200.0       # 句首 ±200ms 内满分
START_ZERO_MS = 800.0       # 到 800ms 线性到 0
DUR_FULL = 0.06             # 句长 ±6% 内满分
DUR_ZERO = 0.26             # 到 ±26% 线性到 0
RESID_FULL_MS = 100.0       # 句内残差 ≤100ms 满分
RESID_ZERO_MS = 400.0       # 到 400ms 线性到 0
MIN_PHRASES = 3
MIN_MATCHED_PER_PHRASE = 2


def _get(obj: Any, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _ramp(value: float, full: float, zero: float) -> float:
    """value 越接近 0 越好；full 内满分，zero 外 0，中间线性。"""
    v = abs(float(value))
    if v <= full:
        return 1.0
    if v >= zero:
        return 0.0
    return 1.0 - (v - full) / max(1e-9, zero - full)


def _ref_notes(ref) -> list:
    return list(getattr(ref, "notes", None) or [])


def _ref_start_end(note) -> tuple[float, float]:
    return float(_get(note, "start_ms", 0) or 0), float(_get(note, "end_ms", 0) or 0)


def build_phrases(ref, lyrics: Optional[Iterable[dict]] = None) -> list[dict]:
    """返回 [{text, from, to, start_ms, end_ms, note_indices}]。

    优先级：
    ① 模板的 Whisper 歌词行（start_ms/end_ms）——整首歌
    ② ReferenceMelody.lyric_lines（from/to）——内置曲目
    ③ 参考音符之间的长停顿（>400ms）——没有歌词时的兜底
    """
    notes = _ref_notes(ref)
    if not notes:
        return []
    n = len(notes)
    out: list[dict] = []

    lyrics = [x for x in (lyrics or []) if isinstance(x, dict)]
    if lyrics:
        for line in lyrics:
            ls = float(line.get("start_ms") or 0)
            le = float(line.get("end_ms") or 0)
            if le <= ls:
                continue
            idx = [i for i, x in enumerate(notes)
                   if ls <= _ref_start_end(x)[0] < le]
            if not idx:
                continue
            out.append({
                "index": len(out),
                "text": str(line.get("text") or "").strip(),
                "from": idx[0], "to": idx[-1],
                "start_ms": ls, "end_ms": le,
                "note_indices": idx,
            })
        if out:
            return out

    # 内置曲目的 from/to（含端点）
    for line in (getattr(ref, "lyric_lines", None) or []):
        if not isinstance(line, dict):
            continue
        try:
            a = int(line.get("from")); b = int(line.get("to"))
        except (TypeError, ValueError):
            continue
        if a < 0 or b < a or b >= n:
            continue
        idx = list(range(a, b + 1))
        out.append({
            "index": len(out),
            "text": str(line.get("text") or "").strip(),
            "from": a, "to": b,
            "start_ms": _ref_start_end(notes[a])[0],
            "end_ms": _ref_start_end(notes[b])[1],
            "note_indices": idx,
        })
    if out:
        return out

    # 兜底：按参考音符之间的停顿切
    cur = [0]
    for i in range(1, n):
        gap = _ref_start_end(notes[i])[0] - _ref_start_end(notes[i - 1])[1]
        if gap > 400:
            out.append(_phrase_from_indices(notes, cur, ""))
            cur = [i]
        else:
            cur.append(i)
    if cur:
        out.append(_phrase_from_indices(notes, cur, ""))
    for i, x in enumerate(out):
        x["index"] = i
    return [x for x in out if x]


def _phrase_from_indices(notes, idx, text):
    if not idx:
        return None
    return {
        "text": text,
        "from": idx[0], "to": idx[-1],
        "start_ms": _ref_start_end(notes[idx[0]])[0],
        "end_ms": _ref_start_end(notes[idx[-1]])[1],
        "note_indices": list(idx),
    }


def _matched_pairs(aligned) -> list[tuple[int, float, float]]:
    """[(ref_index, user_start_ms, user_end_ms)]。"""
    out = []
    for m in aligned or []:
        ri = _get(m, "ref_note_index")
        us = _get(m, "start_ms")
        if ri is None or us is None:
            continue
        ue = _get(m, "end_ms", us)
        out.append((int(ri), float(us), float(ue)))
    return out


def _robust_global_line(pairs, ref) -> tuple[float, float]:
    """稳健拟合 user_start ≈ a·ref_start + b；返回 (a,b)。"""
    if len(pairs) < 4:
        return 1.0, 0.0
    notes = _ref_notes(ref)
    rs = np.array([_ref_start_end(notes[i])[0] for i, _, _ in pairs
                   if 0 <= i < len(notes)], dtype=float)
    us = np.array([u for i, u, _ in pairs if 0 <= i < len(notes)], dtype=float)
    if rs.size < 4:
        return 1.0, 0.0
    a, b = np.polyfit(rs, us, 1)
    if not np.isfinite(a) or not np.isfinite(b):
        a, b = 1.0, float(np.median(us - rs))
    # 两轮 MAD 去离群，避免错配音把全曲斜率带歪
    for _ in range(2):
        r = us - (a * rs + b)
        med = float(np.median(r))
        mad = float(np.median(np.abs(r - med))) or 1.0
        keep = np.abs(r - med) <= 4.5 * mad
        if keep.sum() < max(4, len(rs) // 2):
            break
        a, b = np.polyfit(rs[keep], us[keep], 1)
    return float(a), float(b)


def _score_phrase(phrase, pairs, ref, a: float, b: float) -> Optional[dict]:
    notes = _ref_notes(ref)
    wanted = set(phrase["note_indices"])
    ps = [(i, u, e) for i, u, e in pairs if i in wanted]
    if len(ps) < MIN_MATCHED_PER_PHRASE:
        return None
    ps.sort(key=lambda x: x[1])
    matched_idx = [i for i, _, _ in ps]
    ref_start = min(_ref_start_end(notes[i])[0] for i in matched_idx)
    ref_end = max(_ref_start_end(notes[i])[1] for i in matched_idx)
    user_start = min(u for _, u, _ in ps)
    user_end = max(e for _, _, e in ps)
    exp_start = a * ref_start + b
    start_dev = user_start - exp_start
    ref_dur = max(1.0, ref_end - ref_start)
    exp_dur = max(1.0, a * ref_dur)
    dur_ratio = (user_end - user_start) / exp_dur

    residual = None
    local_tempo = None
    if len(ps) >= 3:
        rr = np.array([_ref_start_end(notes[i])[0] for i in matched_idx], dtype=float)
        uu = np.array([u for _, u, _ in ps], dtype=float)
        try:
            pa, pb = np.polyfit(rr, uu, 1)
            if np.isfinite(pa) and np.isfinite(pb) and pa > 0:
                resid = uu - (pa * rr + pb)
                residual = float(np.sqrt(np.mean(resid ** 2)))
                local_tempo = float(pa)
        except Exception:  # noqa: BLE001
            residual = None

    start_s = _ramp(start_dev, START_FULL_MS, START_ZERO_MS)
    dur_s = _ramp(dur_ratio - 1.0, DUR_FULL, DUR_ZERO)
    if residual is None:
        score = 100.0 * (0.55 * start_s + 0.45 * dur_s)
        resid_s = None
    else:
        resid_s = _ramp(residual, RESID_FULL_MS, RESID_ZERO_MS)
        score = 100.0 * (0.35 * start_s + 0.30 * dur_s + 0.35 * resid_s)
    return {
        "line_index": phrase.get("index"),
        "text": phrase.get("text") or "",
        "from": phrase["from"], "to": phrase["to"],
        "start_ms": round(ref_start, 1), "end_ms": round(ref_end, 1),
        "matched_notes": len(ps),
        "start_dev_ms": round(start_dev, 1),
        "duration_ratio": round(dur_ratio, 4),
        "local_tempo": (round(local_tempo, 4) if local_tempo is not None else None),
        "residual_ms": (round(residual, 1) if residual is not None else None),
        "score": round(score, 1),
        "parts": {
            "start": round(start_s * 100, 1),
            "duration": round(dur_s * 100, 1),
            "residual": (round(resid_s * 100, 1) if resid_s is not None else None),
        },
    }


def score_phrases(aligned, ref, lyrics: Optional[Iterable[dict]] = None,
                  min_phrases: int = MIN_PHRASES) -> Optional[dict]:
    """整首歌的句级节奏分；不足 3 句或覆盖太低返回 None。"""
    phrases = build_phrases(ref, lyrics)
    pairs = _matched_pairs(aligned)
    if not phrases or len(pairs) < 4:
        return None
    a, b = _robust_global_line(pairs, ref)
    rows = []
    for ph in phrases:
        r = _score_phrase(ph, pairs, ref, a, b)
        if r is not None:
            rows.append(r)
    if len(rows) < min_phrases:
        return None

    # 长句权重大：一句 20 秒的副歌当然比“哦”更有代表性
    weights = np.array([max(500.0, r["end_ms"] - r["start_ms"]) for r in rows],
                       dtype=float)
    score = float(np.sum(np.array([r["score"] for r in rows]) * weights)
                  / max(1e-9, float(np.sum(weights))))
    mean_abs_start = float(np.sum(
        np.array([abs(r["start_dev_ms"]) for r in rows]) * weights)
        / max(1e-9, float(np.sum(weights))))
    _rw = np.array([max(500.0, r["end_ms"] - r["start_ms"])
                    for r in rows if r.get("residual_ms") is not None], dtype=float)
    _rv = np.array([r["residual_ms"] for r in rows
                    if r.get("residual_ms") is not None], dtype=float)
    mean_residual = (float(np.sum(_rv * _rw) / max(1e-9, float(np.sum(_rw))))
                     if _rv.size else None)
    matched_notes = int(sum(r["matched_notes"] for r in rows))
    total_notes = int(sum(len(p["note_indices"]) for p in phrases))
    return {
        "method": "phrase",
        "score": round(score, 1),
        "phrases": rows,
        "phrase_count": len(rows),
        "phrase_total": len(phrases),
        "phrase_coverage": round(len(rows) / max(1, len(phrases)), 3),
        "note_coverage": round(matched_notes / max(1, total_notes), 3),
        "mean_abs_start_dev_ms": round(mean_abs_start, 1),
        "mean_residual_ms": (round(mean_residual, 1)
                             if mean_residual is not None else None),
        "global_tempo": round(a, 4),
        "global_offset_ms": round(b, 1),
        "thresholds": {
            "start_full_ms": START_FULL_MS, "start_zero_ms": START_ZERO_MS,
            "duration_full": DUR_FULL, "duration_zero": DUR_ZERO,
            "residual_full_ms": RESID_FULL_MS,
            "residual_zero_ms": RESID_ZERO_MS,
        },
    }

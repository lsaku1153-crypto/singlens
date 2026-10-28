"""曲目推荐（P1）—— 只用**本机已有的参考/模板**，不联网搜歌。

推荐依据只有三样：
1. 用户自测的舒适音区（主观，不是声部）；
2. 用户自述的偏好/常练曲目；
3. 本地曲库的真实音高范围。

不做：
- 不按历史分数排名（低分歌不等于该练，高分歌也不等于适合）；
- 不推荐本机没有的歌曲；
- 不把推荐说成“系统判定你只能唱这个”。
"""
from __future__ import annotations

from analysis.comfort import note_name

_FIT_LABEL = {
    "inside": "音域落在舒适音上",
    "near": "大部分音在舒适音上",
    "transpose": "建议移调",
    "unknown": "舒适区未测，先按偏好/常练推荐",
}


def _midis(notes) -> list[float]:
    out = []
    for n in notes or []:
        if isinstance(n, dict):
            v = n.get("pitch_midi", n.get("midi"))
        else:
            v = getattr(n, "pitch_midi", None)
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if 12 <= f <= 108:
            out.append(f)
    return out


def _range(midis):
    if not midis:
        return None
    lo = int(round(min(midis)))
    hi = int(round(max(midis)))
    return lo, hi, note_name(lo), note_name(hi)


def _coverage(midis, ok_midis):
    """候选音的覆盖率。`ok_midis` 是用户标为舒服的具体音，不是连续区间。"""
    if not midis:
        return 0.0
    ok = set(ok_midis or [])
    return sum(1 for m in midis if m in ok) / len(midis)


def _best_shift(midis, ok_midis, span=6):
    best = None
    for shift in range(-span, span + 1):
        cov = _coverage([m + shift for m in midis], ok_midis)
        if best is None or cov > best[0]:
            best = (cov, shift)
    return best  # (coverage, shift)


def recommend(comfort, stated, candidates, max_items=4) -> dict:
    """comfort = build_comfort 的结果；candidates = [{kind,id,title,notes}]。"""
    st = stated or {}
    ok_midis = list((comfort or {}).get("ok_midis") or [])
    if not ok_midis:
        # 兼容 2026-10-28 之前保存的 comfort_json（只有 notes，没有 ok_midis）。
        for n in (comfort or {}).get("notes") or []:
            try:
                if isinstance(n, dict) and n.get("comfort") == "ok":
                    ok_midis.append(int(n.get("midi")))
            except (TypeError, ValueError):
                continue
    has_comfort = bool(ok_midis)
    favorites = [str(x).lower() for x in (st.get("favorite_songs") or []) if str(x).strip()]
    top = str(st.get("top_song") or "").strip().lower()

    out = []
    for c in candidates or []:
        title = str(c.get("title") or "").strip()
        if not title or title.startswith("微练习"):
            continue
        midis = _midis(c.get("notes"))
        rng = _range(midis)
        if not rng:
            continue
        lo, hi, lo_name, hi_name = rng
        if rng[1] - rng[0] > 36:  # 超过 3 个八度，不像单句练习
            continue
        fit = "unknown"
        transpose = 0
        why = ""
        src_kind = str(c.get("source_kind") or "")
        if has_comfort:
            cov = _coverage(midis, ok_midis)
            if cov >= 0.999:
                fit = "inside"
                why = f"音域 {lo_name}–{hi_name}，都在你自测的舒适音上。"
            elif cov >= 0.8:
                fit = "near"
                why = f"音域 {lo_name}–{hi_name}，大部分在舒适音上，少数在边缘。"
            else:
                bcov, shift = _best_shift(midis, ok_midis)
                if shift and bcov >= 0.8 and src_kind != "song_vocal":
                    fit = "transpose"
                    transpose = shift
                    direction = "升" if shift > 0 else "降"
                    why = (f"原调 {lo_name}–{hi_name} 超出舒适音；"
                           f"建议{direction} {abs(shift)} 个半音再试。")
                else:
                    # 歌曲参考的伴奏是原调音频，不能只移参考音高；
                    # 超出舒适区时不硬推，也不给一个前端做不到的移调建议。
                    continue
        else:
            why = "还没测舒适音区；先按你的偏好/常练曲目给一个可试的。"

        tl = title.lower()
        fav = bool(top and top in tl) or any(f and f in tl for f in favorites)
        out.append({
            "kind": c.get("kind") or "builtin",
            "id": c.get("id") or "",
            "title": title,
            "source_kind": src_kind,
            "note_count": len(midis),
            "range": {"low_midi": lo, "high_midi": hi,
                      "low": lo_name, "high": hi_name},
            "fit": fit,
            "fit_label": _FIT_LABEL[fit],
            "transpose": transpose,
            "favorite": fav,
            "why": why,
            "duration_ms": c.get("duration_ms"),
        })

    # 去重：内置曲目的“第一句/前两句/完整四句”算同一首，只留一个；
    # 用户模板仍按标题区分（同歌的主歌/副歌可能是两份不同模板）。
    seen = set()
    uniq = []
    for x in out:
        base = x["title"].split("·")[0].strip() if x["kind"] == "builtin" else x["title"].strip()
        key = (x["kind"], base)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(x)
    rank = {"inside": 0, "near": 1, "transpose": 2, "unknown": 3}
    uniq.sort(key=lambda x: (rank.get(x["fit"], 9), 0 if x["favorite"] else 1,
                             x["note_count"]))
    items = uniq[:max_items]
    if not items:
        reason = ("自测舒适区里没有合适的本地曲目。可以换一个更宽的音区，"
                  "或先移调试一首。" if has_comfort else
                  "本地还没有可推荐的曲目。先上传/保存一首，或在 A 模式练内置曲目。")
    else:
        reason = ""
    return {
        "has_comfort": has_comfort,
        "comfort_summary": (comfort or {}).get("summary") if has_comfort else "",
        "based_on": {
            "goal": st.get("goal") or "",
            "preferred_style": st.get("preferred_style") or [],
            "top_song": st.get("top_song") or "",
        },
        "items": items,
        "reason": reason,
        "disclaimer": ("推荐只按舒适音区、偏好和本地曲库匹配，不按历史分数排名；"
                       "不是“你只能唱这些”。"),
    }

"""舒适音区自测（P1）—— 主观自报，不是声部/绝对音域测量。

流程：用户跟着钢琴音唱几个音，自己标“舒服 / 勉强 / 不舒服”。
我们只汇总“舒服”的那些音，给出一个**主观舒适范围**。

⚠️ 这个结果不能用来判断声部（男高/女中……），也不是医学/声带评估。
"""
from __future__ import annotations

from datetime import datetime, timezone

_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def note_name(midi: int) -> str:
    m = int(midi)
    return f"{_NAMES[m % 12]}{m // 12 - 1}"


def build_comfort(notes, tested_at: str | None = None) -> dict:
    clean = []
    seen = {}
    for x in notes or []:
        if not isinstance(x, dict):
            continue
        try:
            midi = int(x.get("midi"))
        except (TypeError, ValueError):
            continue
        if not (36 <= midi <= 84):
            continue
        c = str(x.get("comfort") or "").strip().lower()
        if c not in ("ok", "hard", "no"):
            continue
        seen[midi] = c
    for midi in sorted(seen):
        clean.append({"midi": midi, "name": note_name(midi),
                      "comfort": seen[midi]})
    ok = [x for x in clean if x["comfort"] == "ok"]
    low = ok[0] if ok else None
    high = ok[-1] if ok else None
    span = (high["midi"] - low["midi"]) if low and high else None
    # ★ 不能只取 min/max：C3 舒服、D3 明确不舒服、E3 舒服时，min/max
    #   会造出一个包含 D3 的假“舒适范围”。但“未测”不是“洞”——
    #   界面只给 8 个稀疏提示音，未测的半音不能当成不舒服；只有用户
    #   在两个舒服音之间明确标了 no/hard，才把这段范围判为不连续。
    blocked = [x for x in clean
               if low and high and low["midi"] < x["midi"] < high["midi"]
               and x["comfort"] in ("no", "hard")]
    has_gap = bool(blocked)
    if len(ok) >= 5:
        conf = "high"
    elif len(ok) >= 3:
        conf = "medium"
    else:
        conf = "low"
    if ok and not has_gap:
        summary = (f"舒适范围约 {low['name']}–{high['name']}"
                   f"（{span} 个半音，主观自测）")
    elif ok:
        names = [x["name"] for x in ok]
        shown = "、".join(names[:6]) + ("…" if len(names) > 6 else "")
        summary = (f"舒适音：{shown}（不连续；中间有标为勉强/不舒服的音，"
                   "不能当成连续音域）")
    else:
        summary = "还没标出足够多的舒服音，暂时不构成舒适范围"
    return {
        "notes": clean,
        "ok_midis": [x["midi"] for x in ok],
        "has_gap": has_gap,
        "blocked_midis": [x["midi"] for x in blocked],
        "low": low,
        "high": high,
        "span_semitones": span,
        "ok_count": len(ok),
        "tested_count": len(clean),
        "confidence": conf,
        "summary": summary,
        "method": "self_report_guided_tone",
        "tested_at": tested_at or datetime.now(timezone.utc).isoformat(),
        "disclaimer": "这是主观舒适范围，不是声部或绝对音域；换一天可能不同。",
    }


def comfort_brief(comfort: dict | None) -> dict:
    if not isinstance(comfort, dict):
        return {}
    out = {
        "summary": comfort.get("summary") or "",
        "confidence": comfort.get("confidence") or "low",
        "tested_count": comfort.get("tested_count") or 0,
    }
    return out

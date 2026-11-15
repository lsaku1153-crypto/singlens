"""报告类工具：全部只读，输入是一份已经算好的 report。

设计边界
--------
· 一个工具回答一类问题，不做 `get_everything()` —— 一次性全给，模型就
  没有"选择"可展示，trace 也退化成一行；
· 不暴露原始 `segments`（整首歌上百条），只给聚合后的前几个音；
· "不存在"不是错误：`get_phrase(99)` 返回 `found:false`，让模型能如实说
  "没有这句"。只有参数错、超时、未知工具才是 error。
"""
from __future__ import annotations

import re

from analysis.agent.contracts import ToolContext, ToolError, integer, obj, text
from analysis.agent.registry import tool

# 问题片段标题里的序号（"第 1–6 个音" / "第 2 句"）。
# 这些数字出现在工具返回里，理应被回答引用；`headline` 是文本字段，
# 数字不会进白名单，所以单独抽出来放 `headline_numbers`。
_HEADLINE_NUM = re.compile(r"第\s*(\d+)(?:\s*[–\-~至]\s*(\d+))?")


def _num(v):
    return (round(float(v), 1)
            if isinstance(v, (int, float)) and not isinstance(v, bool) else None)


def _ratio(v):
    """比率保留 3 位：0.991 四舍五入到 1 位会变成 1.0，
    "比参考快 0.9%" 这个信息就没了。"""
    return (round(float(v), 3)
            if isinstance(v, (int, float)) and not isinstance(v, bool) else None)


def _ms(ms) -> str:
    try:
        ms = max(0, int(ms))
    except (TypeError, ValueError):
        return ""
    return f"{ms // 60000:02d}:{(ms // 1000) % 60:02d}"


def _phrases(rep: dict) -> list[dict]:
    ph = ((rep.get("rhythm") or {}).get("phrase") or {})
    rows = ph.get("phrases") if isinstance(ph, dict) else None
    return [x for x in (rows or []) if isinstance(x, dict)]


def _nums_in(text) -> list[int]:
    """标题/标签里出现的序号（"第 1–6 个音" → [1, 6]）。

    这些数字出现在工具返回里，理应允许被回答引用；但字段本身是文本，
    数字不会进白名单，所以单独抽出来放 `*_numbers`。
    """
    out: list[int] = []
    for m in _HEADLINE_NUM.finditer(str(text or "")):
        for g in m.groups():
            if g is not None and int(g) not in out:
                out.append(int(g))
    return out


def _card_line(rep: dict) -> dict:
    """练习卡里定位到的那一行。

    模式 A/B/C/D 不计算句级节奏，但练习卡仍然会指到"这一遍该练哪一句"。
    没有句级分数时，这是唯一的行级线索；工具不利用它，Agent 就只能说"没测到"。
    """
    card = rep.get("practice_card") or rep.get("practice_plan") or {}
    if not isinstance(card, dict):
        return {}
    ctx = card.get("context") if isinstance(card.get("context"), dict) else {}
    line_no = ctx.get("line_no")
    if not isinstance(line_no, int):
        return {}
    seg = card.get("segment") if isinstance(card.get("segment"), dict) else {}
    start, end = seg.get("start_ms"), seg.get("end_ms")
    time_text = (f"{_ms(start)}–{_ms(end)}"
                 if isinstance(start, (int, float))
                 and isinstance(end, (int, float)) else None)
    return {"line_no": line_no, "text": ctx.get("line"), "time": time_text,
            "start_ms": start, "end_ms": end}


@tool(
    "get_report_overview",
    "这份报告的总体测量：总分、音准、节奏、表现力、对齐可信度，"
    "以及 3 个最需要改的问题片段（时间/音区/偏差）。"
    "任何‘唱得怎么样/哪里最需要改’的问题先调它，再决定看哪一句。",
)
def get_report_overview(ctx: ToolContext) -> dict:
    rep = ctx.report
    p = rep.get("pitch") or {}
    rh = rep.get("rhythm") or {}
    ex = rep.get("expressiveness") or {}
    meta = rep.get("meta") or {}
    align = meta.get("align") or {}
    ratio = align.get("align_ratio")
    low = isinstance(ratio, (int, float)) and ratio < 0.70

    # 问题片段是"没有句级数据"的报告里唯一的逐音线索（模式 A/B/C/D 都靠它）。
    # 只给结构化字段 + 短标题；detail 是自由文本，数字不参与白名单校验。
    problems = []
    for x in (rep.get("problems") or [])[:3]:
        if not isinstance(x, dict):
            continue
        nr = x.get("note_range") if isinstance(x.get("note_range"), list) else []
        nf = nr[0] if len(nr) > 1 else x.get("start_note")
        nt = nr[1] if len(nr) > 1 else x.get("end_note")
        start, end = x.get("start_ms"), x.get("end_ms")
        nums = _nums_in(x.get("headline"))
        problems.append({
            "kind": x.get("kind"),
            "severity": x.get("severity"),
            "headline": x.get("headline"),
            "headline_numbers": nums,
            "time": (f"{_ms(start)}–{_ms(end)}"
                     if isinstance(start, (int, float))
                     and isinstance(end, (int, float)) else None),
            "note_from": nf if isinstance(nf, int) else None,
            "note_to": nt if isinstance(nt, int) else None,
            "median_cents": _num(x.get("median")),
            "max_abs_cents": _num(x.get("max_abs")),
        })

    return {
        "reference": {"title": meta.get("reference_title"),
                      "source": meta.get("reference_source"),
                      "transpose": meta.get("transpose")},
        "total": None if low else _num(rep.get("total")),
        "pitch": {"score": _num(p.get("score")),
                  "accuracy_cents": _num(p.get("accuracy")),
                  "stability_cents": _num(p.get("stability"))},
        "rhythm": {"score": None if low else _num(rh.get("score")),
                   "basis": rh.get("basis"),
                   "accuracy_ms": None if low else _num(rh.get("accuracy")),
                   "stability_ms": None if low else _num(rh.get("stability")),
                   "tempo_ratio": _ratio(rh.get("tempo_ratio"))},
        "expressiveness": _num(ex.get("score")),
        "alignment_ratio": _ratio(ratio),
        "low_alignment_confidence": low,
        "problems": problems,
        "note": ("逐音对齐低于 70%，总分与节奏未评估；"
                 "只有音准/表现力/练习片段可参考。" if low else None),
    }


@tool(
    "list_phrases",
    "按分数排序列出句级结果。用户问‘哪几句最差/最需要改/整体分布’时用。"
    "不要用它猜歌词 —— 找歌词用 search_lyrics。",
    obj({"order": text("worst=分最低优先，best=分最高优先，time=按时间",
                       ["worst", "best", "time"]),
         "limit": integer("返回几句，1–10", 1, 10)}, []),
)
def list_phrases(ctx: ToolContext, order: str = "worst", limit: int = 5) -> dict:
    rows = [r for r in _phrases(ctx.report)
            if isinstance(r.get("score"), (int, float))]
    if not rows:
        hint = _card_line(ctx.report)
        note = "这份报告没有句级分数（可能是逐音对齐太低），不能按句比较。"
        if hint:
            note += f" 练习卡指到第 {hint['line_no']} 句，可用 get_practice_card。"
        return {"count": 0, "phrases": [],
                "practice_line": hint or None, "note": note}
    if order == "time":
        rows.sort(key=lambda x: x.get("start_ms") or 0)
    else:
        rows.sort(key=lambda x: float(x["score"]), reverse=(order == "best"))
    return {
        "count": len(rows),
        "order": order,
        "phrases": [{
            "line_no": int(r.get("line_index", 0)) + 1,
            "text": r.get("text"),
            "score": _num(r.get("score")),
            "time": _ms(r.get("start_ms")),
        } for r in rows[:max(1, min(10, int(limit)))]],
    }


@tool(
    "search_lyrics",
    "用歌词片段找句子（至少 2 个字）。用户凭记忆说‘那句月亮…’时用。",
    obj({"query": text("歌词片段，2–20 字")}),
)
def search_lyrics(ctx: ToolContext, query: str) -> dict:
    q = str(query or "").strip()
    if len(q) < 2:
        raise ToolError("query_too_short", "至少给 2 个字，否则匹配不到。")
    hits = []
    for r in _phrases(ctx.report):
        t = str(r.get("text") or "")
        if q in t:
            hits.append({"line_no": int(r.get("line_index", 0)) + 1,
                         "text": t,
                         "score": _num(r.get("score")),
                         "time": _ms(r.get("start_ms"))})
    return {"query": q, "matches": hits[:5],
            "note": None if hits else "歌词里没有匹配的句子，不要假设它存在。"}


@tool(
    "get_phrase",
    "取某一句的细节：句首偏差、句长比例、句内残差、偏差最大的几个音。"
    "回答‘第 N 句怎么唱/为什么低’之前必须调它。",
    obj({"line_no": integer("句号，从 1 开始", 1, 999)}),
)
def get_phrase(ctx: ToolContext, line_no: int) -> dict:
    rows = _phrases(ctx.report)
    row = None
    for r in rows:
        if int(r.get("line_index", -1)) + 1 == int(line_no):
            row = r
            break
    if row is None:
        card = _card_line(ctx.report)
        # 练习卡指到的就是这一句：没有句级分数，但"这一句的文本/时间"是有的。
        # 返回 found:false 会让模型在"系统自己给的卡"和"查不到"之间打转。
        if card and int(line_no) == card["line_no"]:
            return {"found": True, "source": "practice_card", "line_no": line_no,
                    "text": card.get("text"), "time": card.get("time"),
                    "score": None,
                    "start_ms": card.get("start_ms"), "end_ms": card.get("end_ms"),
                    "note": "这一遍只有逐音对齐，没有句级分数；"
                            "以下是练习卡定位到的这一句。"}
        # 不是 error：模型必须能诚实地说"没有这句"
        note = f"这份报告没有第 {line_no} 句（共 {len(rows)} 句）。"
        if card:
            note += (f" 这一遍是逐音对齐，没有句级分数；"
                     f"练习卡指到第 {card['line_no']} 句，可用 get_practice_card。")
        return {"found": False, "line_no": line_no, "note": note}

    notes = []
    try:
        a, b = int(row.get("from")), int(row.get("to"))
    except (TypeError, ValueError):
        a = b = None
    if a is not None:
        for s in (ctx.report.get("segments") or []):
            if not isinstance(s, dict):
                continue
            idx = s.get("ref_note_index")
            if not isinstance(idx, int) or not (a <= idx <= b):
                continue
            if s.get("pitch_cents_dev") is None and s.get("onset_dev_ms") is None:
                continue
            notes.append({"ref_note_index": idx,
                          "time": _ms(s.get("start_ms")),
                          "pitch_cents": _num(s.get("pitch_cents_dev")),
                          "onset_ms": _num(s.get("onset_dev_ms"))})
        notes.sort(key=lambda x: -abs(x.get("pitch_cents") or 0))

    start, end = row.get("start_ms"), row.get("end_ms")
    time_text = (f"{_ms(start)}–{_ms(end)}"
                 if isinstance(start, (int, float)) and isinstance(end, (int, float))
                 else None)
    return {
        "found": True,
        "line_no": line_no,
        "text": row.get("text"),
        "time": time_text,
        "score": _num(row.get("score")),
        "start_dev_ms": _num(row.get("start_dev_ms")),
        "duration_ratio": _ratio(row.get("duration_ratio")),
        "residual_ms": _num(row.get("residual_ms")),
        "matched_notes": row.get("matched_notes"),
        "notes": notes[:5],
    }


@tool(
    "get_beat_diagnosis",
    "节拍诊断：踩中率、整体相位偏移、最不稳的小节。"
    "用户问‘节奏/拍子/踩不准/抢拍拖拍’时用。",
)
def get_beat_diagnosis(ctx: ToolContext) -> dict:
    b = (ctx.report.get("beat_diagnostic")
         or (ctx.report.get("meta") or {}).get("beat_diagnostic"))
    if not isinstance(b, dict):
        return {"available": False,
                "note": "这一遍没有节拍诊断（没有伴奏节拍网格）。"}
    bars = []
    for x in (b.get("worst_bars") or [])[:3]:
        if not isinstance(x, dict):
            continue
        bars.append({"bar": x.get("bar"),
                     "time": _ms(x.get("start_ms")),
                     "hit_beats": x.get("hit_beats"),
                     "active_beats": x.get("active_beats"),
                     "hit_rate": _num(x.get("hit_rate")),
                     "mean_offset_ms": _num(x.get("mean_offset_ms"))})
    return {"available": True,
            "hit_rate": _num(b.get("hit_rate")),
            "phase_ms": _num(b.get("phase_ms")),
            "stability_ms": _num(b.get("stability_ms")),
            "tempo_bpm": _num(b.get("tempo_bpm")),
            "active_beats": b.get("active_beats"),
            "worst_bars": bars}


@tool(
    "get_practice_card",
    "当前报告建议练的那一小段（含起止时间）。用户问‘那我现在练什么’时用。",
)
def get_practice_card(ctx: ToolContext) -> dict:
    rep = ctx.report
    card = rep.get("practice_card") or rep.get("practice_plan")
    if not isinstance(card, dict) or not card:
        return {"available": False, "note": "这份报告没有练习卡。"}
    seg = card.get("segment") or {}
    c = card.get("context") or {}
    start, end = seg.get("start_ms"), seg.get("end_ms")
    time_text = (f"{_ms(start)}–{_ms(end)}"
                 if isinstance(start, (int, float)) and isinstance(end, (int, float))
                 else None)
    # 时长要**由工具算好**。让模型自己用 end-start 算，会得到一个不在数据里的
    # 数字，被 groundING 判成编造（2026-11-12 在线评测的 practice_005 就是这样）。
    duration_ms = None
    if isinstance(start, (int, float)) and isinstance(end, (int, float)):
        duration_ms = max(0, int(end) - int(start))
    label_text = card.get("label") or card.get("focus")
    return {"available": True,
            "kind": card.get("kind"),
            "label": label_text,
            # 标签可能是"第 15–17 个音"：文本里的序号不进白名单，
            # 不单独给出来，模型一引用就被判编造。
            "label_numbers": _nums_in(label_text),
            "line_no": c.get("line_no"),
            "line": c.get("line"),
            "start_ms": start, "end_ms": end,
            "time": time_text,
            "duration_ms": duration_ms,
            "duration_s": (round(duration_ms / 1000, 1)
                           if duration_ms is not None else None),
            "done": card.get("done")}


@tool(
    "propose_preference",
    "当用户明确说出偏好（‘我更喜欢/我不喜欢/别给我…’）时，提议记住它。"
    "只返回提案，不会保存；不要用它保存问题或临时情绪。",
    obj({"text": text("用户原话里的偏好，≤60 字")}),
)
def propose_preference(ctx: ToolContext, text: str) -> dict:
    t = str(text or "").strip()[:60]
    if not t:
        raise ToolError("empty_text", "text 不能为空。")
    return {"proposal": t, "saved": False,
            "note": "已作为候选返回给界面，用户点‘记住’才会写入画像。"}

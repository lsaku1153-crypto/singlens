"""报告后的追问教练（P0：有证据的 Q&A，不做自由评分）。

设计原则
--------
1. **回答必须有依据**：只从报告里选 1 句 / 1 小节 / 几个音，不把
   500 段 segments 全塞给模型，也不让模型自己编“第 3 句偏低”。
2. **没有 LLM 也能答**：没 key / 断网 / 超时 → 规则回答，UI 标
   `rule_fallback`；不能空白，也不能假装是 AI。
3. **不改分**：问教练只解释已有测量，绝不重新算一个分数。
4. **隐私**：只发报告数字和歌词/时间，不发音频，不发演唱者身份。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from analysis import coach

_CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
           "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _int_cn(s: str) -> int | None:
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if "十" in s:
        a, _, b = s.partition("十")
        tens = _CN_NUM.get(a, 1) if a else 1
        ones = _CN_NUM.get(b, 0) if b else 0
        return tens * 10 + ones
    if len(s) == 1:
        return _CN_NUM.get(s)
    return None


def _fmt_time(ms) -> str:
    try:
        ms = max(0, int(ms))
    except (TypeError, ValueError):
        return ""
    return f"{ms // 60000:02d}:{(ms // 1000) % 60:02d}"


def _num(v):
    return (round(float(v), 1)
            if isinstance(v, (int, float)) and not isinstance(v, bool) else None)


def _short(s, n=80) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def _phrase_rows(rep: dict) -> list[dict]:
    ph = ((rep.get("rhythm") or {}).get("phrase") or {})
    rows = ph.get("phrases") if isinstance(ph, dict) else None
    return [x for x in (rows or []) if isinstance(x, dict)]


def _pick_phrase(rep: dict, question: str, focus: dict | None,
                 allow_worst: bool = True) -> tuple[dict | None, int | None]:
    rows = _phrase_rows(rep)
    line = None
    if isinstance(focus, dict):
        try:
            line = int(focus.get("line_no"))
        except (TypeError, ValueError):
            line = None
    if line is None:
        m = re.search(r"第\s*([0-9一二两三四五六七八九十]+)\s*句", question or "")
        if m:
            line = _int_cn(m.group(1))
    if not rows:
        return None, line
    if line is None:
        # 歌词片段匹配（至少 2 个字，避免“我”这种太泛）
        for r in rows:
            text = str(r.get("text") or "")
            if len(text) >= 2 and text in (question or ""):
                line = int(r.get("line_index", 0)) + 1
                break
    if line is not None:
        for r in rows:
            if int(r.get("line_index", -1)) + 1 == line:
                return r, line
        return None, line
    # 没有指定就取句级分最低的一句（只取有分的）；节奏问题不自动选句。
    if not allow_worst:
        return None, None
    scored = [r for r in rows if isinstance(r.get("score"), (int, float))]
    if not scored:
        return None, None
    r = min(scored, key=lambda x: float(x.get("score")))
    return r, int(r.get("line_index", 0)) + 1


def _line_notes(rep: dict, phrase: dict) -> list[dict]:
    try:
        a = int(phrase.get("from")); b = int(phrase.get("to"))
    except (TypeError, ValueError):
        return []
    segs = [s for s in (rep.get("segments") or [])
            if isinstance(s, dict) and isinstance(s.get("ref_note_index"), int)
            and a <= s["ref_note_index"] <= b]
    out = []
    for s in segs:
        if s.get("pitch_cents_dev") is None and s.get("onset_dev_ms") is None:
            continue
        out.append({
            "note_index": s.get("note_index"),
            "ref_note_index": s.get("ref_note_index"),
            "start_ms": s.get("start_ms"),
            "pitch_cents_dev": _num(s.get("pitch_cents_dev")),
            "onset_dev_ms": _num(s.get("onset_dev_ms")),
        })
    out.sort(key=lambda x: -abs(x.get("pitch_cents_dev") or 0))
    return out[:5]


def _beat_ctx(rep: dict) -> dict | None:
    b = rep.get("beat_diagnostic") or (rep.get("meta") or {}).get("beat_diagnostic")
    if not isinstance(b, dict):
        return None
    bars = []
    for x in (b.get("worst_bars") or [])[:3]:
        if not isinstance(x, dict):
            continue
        bars.append({
            "bar": x.get("bar"),
            "start_ms": x.get("start_ms"), "end_ms": x.get("end_ms"),
            "time": _fmt_time(x.get("start_ms")),
            "active_beats": x.get("active_beats"),
            "hit_beats": x.get("hit_beats"),
            "hit_rate": _num(x.get("hit_rate")),
            "mean_offset_ms": _num(x.get("mean_offset_ms")),
            "stability_ms": _num(x.get("stability_ms")),
        })
    return {
        "hit_rate": _num(b.get("hit_rate")),
        "phase_ms": _num(b.get("phase_ms")),
        "stability_ms": _num(b.get("stability_ms")),
        "active_beats": b.get("active_beats"),
        "tempo_bpm": _num(b.get("tempo_bpm")),
        "worst_bars": bars,
    }


def _practice_ctx(rep: dict, focus_beat: bool) -> dict | None:
    card = (rep.get("beat_practice") if focus_beat
            else rep.get("practice_card")) or rep.get("beat_practice")
    if isinstance(card, dict) and isinstance(card.get("segment"), dict):
        seg = card["segment"]
        return {
            "kind": card.get("kind"),
            "label": card.get("label"),
            "line_no": (card.get("context") or {}).get("line_no"),
            "start_ms": seg.get("start_ms"), "end_ms": seg.get("end_ms"),
            "focus": card.get("focus"),
            "done": card.get("done"),
        }
    plan = rep.get("practice_plan") or {}
    if isinstance(plan, dict) and plan.get("focus"):
        return {"kind": plan.get("kind"), "label": plan.get("focus"),
                "line_no": (plan.get("context") or {}).get("line_no"),
                "focus": plan.get("focus"), "done": plan.get("done")}
    return None


def _explicit_phrase(rep: dict, question: str, focus: dict | None) -> bool:
    if isinstance(focus, dict) and focus.get("line_no") is not None:
        return True
    if re.search(r"第\s*[0-9一二两三四五六七八九十]+\s*句", question or ""):
        return True
    for r in _phrase_rows(rep):
        text = str(r.get("text") or "")
        if len(text) >= 2 and text in (question or ""):
            return True
    return False


def build_context(rep: dict, question: str, focus: dict | None = None) -> dict:
    meta = rep.get("meta") or {}
    p = rep.get("pitch") or {}
    rh = rep.get("rhythm") or {}
    ex = rep.get("expressiveness") or {}
    align = meta.get("align") or {}
    ratio = align.get("align_ratio")
    low_conf = isinstance(ratio, (int, float)) and ratio < 0.70
    q = str(question or "")
    if any(k in q for k in ("节奏", "拍子", "跟拍", "踩", "拖拍", "抢拍")):
        intent = "rhythm"
    elif any(k in q for k in ("音准", "跑调", "偏高", "偏低", "高音", "低音")):
        intent = "pitch"
    elif any(k in q for k in ("唱法", "方式", "风格", "处理", "怎么唱",
                               "感情", "表达", "适合", "探索", "味道", "感觉")):
        intent = "style"
    elif any(k in q for k in ("其它", "还有", "哪里", "整体", "别的")):
        intent = "overview"
    else:
        intent = "overview"

    explicit = _explicit_phrase(rep, q, focus)
    # 没有点名某一句/某段歌词时，“唱法/风格/怎么唱”走自由教练：
    # 可以讲通用声乐/风格知识，不强行拿最差的一句当答案。
    scope = "general" if (intent == "style" and not explicit) else "report"
    if scope == "general":
        phrase, line_no = None, None
        notes = []
        beat = None
    else:
        phrase, line_no = _pick_phrase(rep, q, focus,
                                       allow_worst=(intent != "rhythm"))
        notes = _line_notes(rep, phrase) if phrase else []
        missing_line = line_no is not None and phrase is None
        beat = None if missing_line else _beat_ctx(rep)

    overview = {
        "reference_title": meta.get("reference_title"),
        "reference_source": meta.get("reference_source"),
        "total": _num(rep.get("total")),
        "pitch_score": _num(p.get("score")),
        "pitch_accuracy_cents": _num(p.get("accuracy")),
        "pitch_stability_cents": _num(p.get("stability")),
        "rhythm_score": _num(rh.get("score")),
        "rhythm_basis": rh.get("basis"),
        "rhythm_accuracy_ms": _num(rh.get("accuracy")),
        "rhythm_stability_ms": _num(rh.get("stability")),
        "tempo_ratio": _num(rh.get("tempo_ratio")),
        "expressiveness_score": _num(ex.get("score")),
        "align_ratio": _num(ratio),
        "low_alignment_confidence": bool(low_conf),
        "measurement_note": None,
        "self_reference": bool(meta.get("self_reference")),
        "reference_lyrics_count": meta.get("reference_lyrics_count"),
        "transpose": meta.get("transpose"),
        "accompaniment_mode": bool(meta.get("accompaniment_mode")),
    }
    phrase_out = None
    if phrase:
        phrase_out = {
            "line_no": line_no,
            "text": phrase.get("text"),
            "start_ms": phrase.get("start_ms"), "end_ms": phrase.get("end_ms"),
            "time": f"{_fmt_time(phrase.get('start_ms'))}–{_fmt_time(phrase.get('end_ms'))}",
            "score": _num(phrase.get("score")),
            "start_dev_ms": _num(phrase.get("start_dev_ms")),
            "duration_ratio": _num(phrase.get("duration_ratio")),
            "residual_ms": _num(phrase.get("residual_ms")),
            "matched_notes": phrase.get("matched_notes"),
            "notes": notes,
        }
    # 与 UI 同一门槛：逐音对齐低且没有句级分时，节奏/总分是“未评估”，
    # 不能让教练把旧的 18.6/56.2 当成有效测量复述。
    if low_conf and not phrase_out:
        overview["rhythm_score"] = None
        overview["rhythm_accuracy_ms"] = None
        overview["rhythm_stability_ms"] = None
        overview["total"] = None
        overview["measurement_note"] = (
            f"逐音对齐仅 {(ratio or 0) * 100:.0f}%，本次节奏与总分未评估；"
            "只能参考音准、表现力和已定位的练习片段。")
    problems = []
    for x in (rep.get("problems") or [])[:3]:
        if not isinstance(x, dict):
            continue
        problems.append({
            "headline": _short(x.get("headline"), 50),
            "detail": _short(x.get("detail"), 90),
            "start_note": x.get("start_note"), "end_note": x.get("end_note"),
        })
    scored = [r for r in _phrase_rows(rep)
              if isinstance(r.get("score"), (int, float))]
    scored.sort(key=lambda x: float(x.get("score")))
    worst_phrases = [{
        "line_no": int(r.get("line_index", 0)) + 1,
        "text": r.get("text"),
        "score": _num(r.get("score")),
        "start_ms": r.get("start_ms"),
        "time": _fmt_time(r.get("start_ms")),
    } for r in scored[:3]]
    style = {
        "tempo_ratio": overview["tempo_ratio"],
        "tempo_bpm": _num(((rep.get("beat_diagnostic") or {}).get("tempo_bpm")
                           if isinstance(rep.get("beat_diagnostic"), dict) else None)
                          or ((meta.get("beat_diagnostic") or {}).get("tempo_bpm")
                              if isinstance(meta.get("beat_diagnostic"), dict) else None)),
        "reference_source": overview["reference_source"],
        "transpose": overview["transpose"],
        "reference_max_gap_ms": meta.get("reference_max_gap_ms"),
        "lyrics_count": overview["reference_lyrics_count"],
    }
    practice = (None if scope == "general"
                else _practice_ctx(rep, intent == "rhythm" or bool(beat and not phrase)))
    evidence = []
    if scope == "general":
        evidence.append({
            "kind": "reference",
            "label": f"参考：{overview.get('reference_title') or '这首歌'} · 自由教练",
        })
    if phrase_out:
        evidence.append({
            "kind": "phrase",
            "label": f"第 {line_no} 句 · {phrase_out['time']}",
            "line_no": line_no, "start_ms": phrase_out["start_ms"],
            "end_ms": phrase_out["end_ms"],
        })
        if notes:
            top = notes[0]
            evidence.append({
                "kind": "pitch",
                "label": f"第 {top.get('ref_note_index', '?')} 个参考音 · "
                         f"{top.get('pitch_cents_dev')} 音分",
                "start_ms": top.get("start_ms"),
            })
    if beat and (intent == "rhythm" or not phrase_out):
        b0 = (beat.get("worst_bars") or [{}])[0]
        evidence.append({
            "kind": "beat",
            "label": f"{b0.get('time') or '节拍'} · 命中 {b0.get('hit_rate')}%（±120ms）",
            "start_ms": b0.get("start_ms"),
        })
    if practice:
        evidence.append({"kind": "practice", "label": practice.get("label") or "练习片段"})
    return {
        "intent": intent,
        "scope": scope,
        "requested_line": line_no,
        "overview": overview,
        "phrase": phrase_out,
        "problems": problems,
        "worst_phrases": worst_phrases,
        "beat": beat if (intent == "rhythm" or not phrase_out) else None,
        "style": style,
        "practice": practice,
        "evidence": evidence,
        "question": q[:300],
    }


def _answer_general_rule(ctx: dict) -> dict:
    """没有 LLM 时的自由教练：给路线，不装成测量结论。"""
    o = ctx["overview"]
    style = ctx.get("style") or {}
    ref = o.get("reference_title") or "这首歌"
    parts = [
        f"不用把「{ref}」定死成一种唱法，可以先试两条路线：",
        "A. 贴原唱：以连奏为主，句尾收气、少加装饰；先把句首和句长按参考做稳。",
        "B. 个人表达：保留一点气声或轻微延迟，但每句只做一个变化，"
        "不要整首同时加气声、滑音和强弱。",
    ]
    if style.get("tempo_ratio") is not None:
        parts.append(f"这一遍整体速度是参考的 {style['tempo_ratio']:.2f} 倍；"
                     "先保持这个速度，再决定要不要更自由。")
    parts.append("你更想「像原唱」还是「唱出自己的感觉」？我可以按这个方向继续拆。")
    return {
        "answer": "\n".join(parts),
        "suggestions": [
            "我想更贴原唱，先改哪里？",
            "我想唱出自己的感觉，怎么加才不油？",
            "第 3 句具体怎么处理？",
        ][:3],
        "practice": None,
        "mode": "rule_fallback",
    }


def _answer_rule(ctx: dict) -> dict:
    if ctx.get("scope") == "general":
        return _answer_general_rule(ctx)
    o = ctx["overview"]; p = ctx["phrase"]; b = ctx.get("beat")
    parts = []
    suggestions = []
    requested = ctx.get("requested_line")
    if o.get("low_alignment_confidence") and not p:
        parts.append(f"这次逐音对齐只有 {(o.get('align_ratio') or 0) * 100:.0f}%，"
                     "逐音判断不可靠；我只能给保守的练习建议，不能说你唱得差。")
    if p:
        t = p.get("text") or ""
        parts.append(f"第 {p['line_no']} 句「{t}」在 {p['time']}。")
        reasons = []
        if isinstance(p.get("start_dev_ms"), (int, float)) and abs(p["start_dev_ms"]) >= 150:
            reasons.append(("句首晚" if p["start_dev_ms"] > 0 else "句首早")
                           + f" {abs(p['start_dev_ms']) / 1000:.2f}s")
        if isinstance(p.get("duration_ratio"), (int, float)):
            dr = p["duration_ratio"]
            if abs(dr - 1) >= 0.06:
                reasons.append(("句长拖" if dr > 1 else "句长赶")
                               + f" {abs(dr - 1) * 100:.0f}%")
        if isinstance(p.get("residual_ms"), (int, float)) and p["residual_ms"] >= 100:
            reasons.append(f"句内残差 {p['residual_ms']:.0f}ms")
        if reasons:
            parts.append("主要问题是：" + "、".join(reasons) + "。")
        else:
            parts.append("这句的句首/句长没有明显问题。")
        if p.get("notes"):
            top = p["notes"][0]
            if top.get("pitch_cents_dev") is not None:
                parts.append(f"句内偏差最大的音在 {_fmt_time(top.get('start_ms'))}，"
                             f"约 {top['pitch_cents_dev']:+.0f} 音分。")
        if reasons and ("句首" in reasons[0]):
            suggestions.append("先只改进唱：听 4 拍预备，第 5 拍第一个字落下。")
        elif reasons:
            suggestions.append("先用原速跟这一句，不要整句放慢；只盯句尾。")
        else:
            suggestions.append("这句可以保留，先把下一句的进唱唱稳。")
        if ctx.get("intent") == "style":
            parts.append("唱法上先别换音色：按参考的连奏/断句唱，"
                         "把句首和句长稳住，再谈装饰音。")
    elif b:
        parts.append(f"节拍诊断：扣掉整体偏移后踩中 {b.get('hit_rate')}%"
                     f"（平均{'晚' if (b.get('phase_ms') or 0) >= 0 else '早'} "
                     f"{abs(b.get('phase_ms') or 0):.0f}ms）。")
        wb = (b.get("worst_bars") or [None])[0]
        if wb:
            parts.append(f"最不稳的是 {wb.get('time')}，"
                         f"{wb.get('hit_beats')}/{wb.get('active_beats')} 拍踩中。")
            suggestions.append(f"先只练 {wb.get('time')} 这几小节，每拍念「哒」。")
    if ctx.get("intent") == "overview":
        others = [x for x in (ctx.get("worst_phrases") or [])
                  if not p or x.get("line_no") != p.get("line_no")][:2]
        if others:
            txt = "、".join(
                f"第 {x['line_no']} 句（{x.get('score')} 分）" for x in others)
            parts.append(f"其它相对靠后的是：{txt}。")
    if not p and not b:
        if isinstance(requested, int):
            parts.append(f"这条报告没有第 {requested} 句的句级数据，不能判断这一句；"
                         "它可能来自旧报告，或这次逐音对齐太低。")
        pr = ctx.get("practice")
        if pr:
            parts.append(f"报告里能定位到的练习片段是「{pr.get('label') or '这一段'}」。")
            suggestions.append("先按这个片段练；它比泛泛地重唱整首更具体。")
        probs = ctx.get("problems") or []
        if probs:
            parts.append(f"报告里已定位到的问题：{probs[0].get('headline') or '有一段偏差'}。")
    if not parts:
        parts.append("这次报告里没有可定位的句子或节拍，先换一个能对上的短参考再问。")
    if ctx.get("practice"):
        suggestions.append("用报告里的「只练这一段」入口，练完再看 before/after。")
    return {
        "answer": "\n".join(parts),
        "suggestions": suggestions[:3],
        "practice": ctx.get("practice"),
        "mode": "rule_fallback",
    }


_SYSTEM = """你是 SingLens 的跟唱教练，正在回答用户对一份测量报告的追问。
硬性要求：
1. 只能使用 DATA 里的数字和句子；DATA 没有的，明说“这次没测到”。
2. 不重新评分，不承诺“练几次能到多少分”，不诊断声带疾病。
3. 测量和建议分开：先说数据，再说一个具体动作。
4. 提到哪一句/哪一拍时，必须带句号或时间；不确定用户指哪句时先问。
5. 用第二人称、中文口语；只讲 1–2 个重点，不要面面俱到。
只输出一个 JSON 对象：
{"answer":"2–4 句，120–260 字","suggestions":["1 个立刻能做的动作","最多 3 条"]}
不要 markdown 代码块。"""

_SYSTEM_GENERAL = """你是 SingLens 的音乐教练，正在陪用户探索一首歌的唱法/风格/表达。
你可以讲通用声乐与音乐处理知识，但必须区分「报告测量」和「一般建议」。
硬性要求：
1. 先回答用户问的唱法/风格，不要一上来复述分数；报告只作补充。
2. 报告里的数字只能引用 DATA；DATA 没有的不要编。
3. 不要断言用户的声部、音色或“天生适合什么”；可以给 2–3 条路线和取舍。
4. 先问目标/偏好（更像原唱 / 自己的感觉），或给用户两个可试方向。
5. 不诊断声带疾病，不承诺结果；不要用“你必须”“唯一正确”这种口气。
6. 中文口语，具体到可试的动作；不要只写“多练气息”。
只输出一个 JSON 对象：
{"answer":"2–4 句，120–260 字","suggestions":["一个可试的方向","最多 3 条"]}
不要 markdown 代码块。"""


def _answer_llm(ctx: dict, history: list) -> tuple[dict | None, str]:
    key = coach.load_key()
    if not key:
        return None, "没配 API key"
    base = (os.environ.get("SINGLENS_LLM_BASE") or coach.DEFAULT_BASE).rstrip("/")
    model = os.environ.get("SINGLENS_LLM_MODEL") or coach.DEFAULT_MODEL
    hist = []
    for m in (history or [])[-4:]:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
            hist.append({"role": m["role"], "content": str(m.get("content") or "")[:240]})
    payload = {k: ctx.get(k) for k in
               (("scope", "overview", "style")
                if ctx.get("scope") == "general" else
                ("scope", "intent", "overview", "phrase", "worst_phrases",
                 "beat", "style", "practice", "problems"))}
    user = ("DATA（JSON）：\n" + json.dumps(payload, ensure_ascii=False)
            + "\n\n用户追问：" + (ctx.get("question") or ""))
    body = {
        "model": model,
        "messages": [{"role": "system",
                       "content": (_SYSTEM_GENERAL if ctx.get("scope") == "general"
                                   else _SYSTEM)}]
                    + hist + [{"role": "user", "content": user}],
        "temperature": 0.55 if ctx.get("scope") == "general" else 0.45,
        # deepseek-flash 会先产生 reasoning tokens；500 会被推理吃满、正文为空。
        "max_tokens": 1500,
    }
    try:
        resp = coach._post(f"{base}/chat/completions", key, body, 20.0)
        raw = (resp["choices"][0]["message"]["content"] or "").strip()
    except Exception as e:  # noqa: BLE001
        return None, f"LLM 调用失败：{str(e)[:100]}"
    a, b = raw.find("{"), raw.rfind("}")
    if a >= 0 and b > a:
        raw = raw[a:b + 1]
    try:
        obj = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None, "LLM 返回不是 JSON"
    ans = str(obj.get("answer") or "").strip() if isinstance(obj, dict) else ""
    if not ans:
        return None, "LLM 返回空回答"
    bad = coach._violates(ans)
    if bad:
        return None, f"LLM 内容不合规（{bad}）"
    sug = [str(x).strip()[:80] for x in (obj.get("suggestions") or [])
           if str(x).strip()][:3]
    return {"answer": ans[:900], "suggestions": sug}, ""


def chat(rep: dict, question: str, history: list | None = None,
         focus: dict | None = None) -> dict:
    t0 = time.time()
    ctx = build_context(rep, question, focus)
    rule = _answer_rule(ctx)
    out = dict(rule)
    out.update({
        "scope": ctx.get("scope") or "report",
        "evidence": ctx.get("evidence") or [],
        "practice": ctx.get("practice"),
        "followups": (
            ["我想更贴原唱，先改哪里？", "我想唱出自己的感觉，怎么加才不油？",
             "第 3 句具体怎么处理？"]
            if ctx.get("scope") == "general" else
            (["这句只练进唱怎么练？", "下一句怎么样？", "用哪种唱法更好？"]
             if ctx.get("phrase") else
             (["这几小节怎么练？", "其它地方怎么样？", "用哪种唱法更好？"]
              if ctx.get("beat") else
              ["哪里最需要改？", "节奏哪里最不稳？", "用哪种唱法更好？"]))
        ),
    })
    if not ctx["overview"].get("self_reference"):
        got, why = _answer_llm(ctx, history or [])
        if got:
            out["answer"] = got["answer"]
            if got.get("suggestions"):
                out["suggestions"] = got["suggestions"]
            out["mode"] = "llm"
        else:
            out["fallback_reason"] = why
    out["latency_ms"] = int((time.time() - t0) * 1000)
    return out

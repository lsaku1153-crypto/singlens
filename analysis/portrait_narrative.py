"""让 V 用一段话讲画像 —— **只在用户点按钮时调用**，失败退回规则版。

约束（都是为了保证不编）：
· 只给模型画像里已经有的事实和规则版叙事；
· 不允许新增阿拉伯数字（输出里的数字必须出现在输入事实中）；
· 不允许声部/人格判断、不允许“你总是/你天生”；
· 返回 `mode=rule_fallback` 时前端必须标明是规则版。
"""
from __future__ import annotations

import json
import os
import re

from analysis import coach
try:
    from analysis import grounding as _grounding
except ImportError:  # pragma: no cover
    import grounding as _grounding

MAX_CHARS = 260
_BANNED = ("你总是", "你天生", "声部", "男高", "男中", "女高", "女中",
           "性格", "内向", "外向", "你只能")


def _clean(text: str) -> str:
    t = str(text or "").strip()
    t = re.sub(r"^```(?:text|markdown)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    t = t.strip().strip('"“”')
    t = re.sub(r"\s+", " ", t)
    # 不在校验前截断：否则超长内容会被静默切短并通过“长度”检查。
    return t


def facts(portrait: dict) -> dict:
    """只抽能给模型看的事实；不把整份报告/录音塞过去。"""
    if not portrait:
        return {}
    st = portrait.get("stated") or {}
    ob = portrait.get("observed") or {}
    return {
        "sessions": (portrait.get("sample") or {}).get("sessions"),
        "songs": (portrait.get("sample") or {}).get("songs"),
        "goal": st.get("goal") or "",
        "preferred_style": st.get("preferred_style") or [],
        "dislikes": st.get("dislikes") or [],
        "notes": (st.get("notes") or [])[:3],
        "observed": {
            k: (ob.get(k) or {}).get("label")
            for k in ("pitch", "stability", "tempo", "brightness", "dynamic", "onset")
            if (ob.get(k) or {}).get("label")
        },
        "comfort": (portrait.get("comfort") or {}).get("summary") or "",
        "trends": [
            {"song": t.get("song"), "delta": t.get("delta"), "n": t.get("n")}
            for t in (portrait.get("trends") or [])[:2]
        ],
        "rule_narrative": portrait.get("narrative") or "",
    }


def _validate(text: str, allowed: set[float]) -> str:
    if not text or len(text) < 10:
        return "内容过短"
    if len(text) > MAX_CHARS:
        return "内容过长"
    for w in _BANNED:
        if w in text:
            return f"出现禁止词：{w}"
    bad = _grounding.unsupported_numbers(text, allowed)
    if bad:
        return "出现输入里没有的数字：" + "、".join(bad[:3])
    return ""


def _call(facts_obj: dict, key: str) -> str:
    base = os.environ.get("SINGLENS_LLM_BASE") or coach.DEFAULT_BASE
    model = os.environ.get("SINGLENS_LLM_MODEL") or coach.DEFAULT_MODEL
    system = (
        "你是 V，SingLens 的跟唱教练。现在你要用 2–3 句话，讲你从练习画像里"
        "看到的这个人。只根据用户提供的事实，不要新增数字，不要推断声部、"
        "音色身份、年龄、性别或性格；用‘从这几次看/目前’这种留余地的说法，"
        "不要说‘你总是/你天生’。直接输出一段中文，不要 JSON、标题或列表。"
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(
                {"facts": facts_obj,
                 "请参考规则版叙事（可以改写，但不要加新事实）":
                     facts_obj.get("rule_narrative", "")},
                ensure_ascii=False)},
        ],
        "max_tokens": 1200,
        "temperature": 0.4,
    }
    data = coach._post(f"{base}/chat/completions", key, body,
                       coach.TIMEOUT_S)
    return ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""


def build(portrait: dict) -> dict:
    """返回 `{narrative, mode, reason}`；永远不抛异常。"""
    rule = (portrait or {}).get("narrative") or ""
    facts_obj = facts(portrait)
    allowed = _grounding.allowed_numbers(facts_obj)
    key = coach.load_key()
    if not key:
        return {"narrative": rule, "mode": "rule_fallback",
                "reason": "未配置 LLM key，先用规则版"}
    try:
        raw = _call(facts_obj, key)
        text = _clean(raw)
        bad = _validate(text, allowed)
        if bad:
            return {"narrative": rule, "mode": "rule_fallback",
                    "reason": f"LLM 版没通过检查（{bad}），先用规则版"}
        return {"narrative": text, "mode": "llm", "reason": ""}
    except Exception as e:  # noqa: BLE001
        return {"narrative": rule, "mode": "rule_fallback",
                "reason": f"LLM 调用失败：{str(e)[:80]}"}

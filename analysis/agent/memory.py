"""会话记忆：把更早的追问压成一句"学生问过什么"。

为什么是**抽取式**而不是让模型总结：
  · 模型总结会写进新数字和新结论 —— 而这一层不在 `verify()` 的检查范围内，
    等于开了一个绕过校验的后门；
  · 抽取只拼接用户原话，不可能产生新数字，也不会把教练的旧结论当成新事实；
  · 不额外调一次模型，长会话不会多花钱。

边界：只压缩"问题"，不压缩"回答"。最近几轮的回答仍然原样进上下文；
更早的回答价值低，丢了比留着更安全。
"""
from __future__ import annotations

import re

MAX_SUMMARY_CHARS = 200
MAX_RECENT = 4          # 原样保留的最近轮数（1 轮 = user + assistant）
MAX_ASK_CHARS = 40


def _asks(history: list[dict] | None) -> list[str]:
    out = []
    for m in history or []:
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        t = re.sub(r"\s+", " ", str(m.get("content") or "")).strip()
        if not t:
            continue
        if len(t) > MAX_ASK_CHARS:
            t = t[:MAX_ASK_CHARS - 1] + "…"
        out.append(t)
    return out


def summarize(history: list[dict] | None,
              max_chars: int = MAX_SUMMARY_CHARS) -> str:
    """从新到旧装问题，装不下就丢最早的。返回空串表示没有可压缩内容。"""
    asks = _asks(history)
    kept: list[str] = []
    total = 0
    for t in reversed(asks):
        add = len(t) + (1 if kept else 0)
        if total + add > max_chars:
            break
        kept.append(t)
        total += add
    kept.reverse()
    if not kept:
        return ""
    prefix = "（更早的问题略）" if len(kept) < len(asks) else ""
    return prefix + "；".join(kept)

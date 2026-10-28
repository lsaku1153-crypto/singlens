"""数字 grounding：LLM 回答里的数字必须能在本次输入里找到。

为什么单独一处
--------------
主报告、追问教练、画像叙事原来各写一套检查（甚至没写）。三处都要遵守
同一条规矩：**模型不能编造“第 3 句低了 35 音分”**。把允许集合的生成与
数字提取收敛到这里，避免“主报告修了、追问忘了”。

边界
----
- 允许集合来自本次报告 / DATA / 画像事实，不是全局数字表；
- 阿拉伯数字、以及两位以上的中文数字（如“九十九”“三十五”）都检查；
- 时间、句号、百分比、音分等只要数字本身在输入里就放行；
- 单个中文数词（“一次”“两句”）不检查 —— 它们是普通量词，不是测量值，
  强拦会把正常中文误杀。
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

_ARABIC = re.compile(r"(?<![\d.])([+-]?\d+(?:\.\d+)?)(?![\d.])")
_CN_DIGITS = "零一二两三四五六七八九"
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}
_CN_NUM = re.compile(r"[零一二两三四五六七八九十百千万]+")


def _parse_cn(s: str) -> int | None:
    """解析常见中文整数（几百/几十/几万）；失败返回 None。"""
    if not s:
        return None
    total = 0
    section = 0
    num = 0
    for ch in s:
        if ch in _CN_DIGITS:
            num = 1 if ch == "两" else _CN_DIGITS.index(ch)
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            if unit == 10000:
                section = (section + num) * unit
                total += section
                section = 0
            else:
                section += (num or 1) * unit
            num = 0
        else:
            return None
    return total + section + num


def _add_number(out: set[float], v: Any) -> None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return
    if not math.isfinite(f):
        return
    out.add(round(f, 6))
    out.add(round(abs(f), 6))
    # 比率 0–1 与百分数 0–100 是同一件事，允许两种写法。
    if 0 <= f <= 1:
        out.add(round(f * 100, 6))
    if 0 <= f <= 100:
        out.add(round(f / 100, 6))


def _walk(obj: Any, out: set[float]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            _walk(k, out)
            _walk(v, out)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            _walk(v, out)
    elif isinstance(obj, bool) or obj is None:
        return
    elif isinstance(obj, (int, float)):
        _add_number(out, obj)
    elif isinstance(obj, str):
        for m in _ARABIC.finditer(obj):
            _add_number(out, m.group(1))


def allowed_numbers(source: Any) -> set[float]:
    """从本次 DATA / payload / facts 里收集所有允许出现的数字。"""
    out: set[float] = set()
    _walk(source, out)
    # json.dumps 会把 0.80 变成 0.8，把嵌套结构压平；再扫一遍最稳。
    try:
        _walk(json.dumps(source, ensure_ascii=False), out)
    except (TypeError, ValueError):
        pass
    return out


def _matches(value: float, allowed: set[float]) -> bool:
    for a in allowed:
        if abs(value - a) <= max(0.01, abs(a) * 1e-4):
            return True
    return False


def unsupported_numbers(text: str, allowed: set[float]) -> list[str]:
    """返回回答里出现、但输入里没有的数字（按原文片段）。"""
    bad: list[str] = []
    seen: set[str] = set()
    for m in _ARABIC.finditer(text or ""):
        raw = m.group(1)
        try:
            v = float(raw)
        except ValueError:
            continue
        if not _matches(v, allowed):
            key = raw
            if key not in seen:
                seen.add(key)
                bad.append(raw)
    for m in _CN_NUM.finditer(text or ""):
        raw = m.group(0)
        # 单个中文数词不查：一次/两句/三遍是量词，不是测量值。
        if len(raw) < 2:
            continue
        v = _parse_cn(raw)
        if v is None:
            continue
        if not _matches(float(v), allowed):
            if raw not in seen:
                seen.add(raw)
                bad.append(raw)
    return bad


def validate_text(text: str, source: Any) -> str:
    """合规返回空串；否则返回一句可展示的原因。"""
    bad = unsupported_numbers(text, allowed_numbers(source))
    if bad:
        return "出现输入里没有的数字：" + "、".join(bad[:3])
    return ""

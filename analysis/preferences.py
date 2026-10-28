"""从用户自己的话里找“可记住的偏好”。

⚠️ 只**提议**，不自动写入画像；前端必须让用户点“记住”才保存。
⚠️ 只抓明确的偏好/限制，不抓“我想知道/想问/怎么唱”这类问题。
"""
from __future__ import annotations

import re

_MAX = 80
_SPLIT = re.compile(r"[，,。；;！!？?\n]+")
_STRIP = re.compile(r"^(?:请|帮我|请帮我|记住|记得|我希望|希望)\s*")
_EXCLUDE = ("想知道", "想问", "想学", "想听", "想看看", "想试", "想练",
            "怎么", "如何", "吗", "呢", "哪一", "什么")
_STRONG = (
    "我喜欢", "我更喜欢", "我不喜欢", "我想要", "我不想要",
    "我想更", "我想唱成", "我更想", "我希望", "我不想", "我不要",
    "不要", "别给", "别把", "不适合我", "适合我", "我的目标",
)


def _clean(s: str) -> str:
    s = s.strip().strip("。；;！!？? ")
    s = _STRIP.sub("", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s[:_MAX]


def extract(text: str) -> list[str]:
    """返回最多 2 条“用户明确说过的偏好”，保持用户原意。"""
    if not text:
        return []
    out = []
    for raw in _SPLIT.split(str(text)):
        clause = _clean(raw)
        if not (3 <= len(clause) <= _MAX):
            continue
        if any(x in clause for x in _EXCLUDE):
            # “我想知道/想问/怎么唱”不是偏好
            continue
        if not any(k in clause for k in _STRONG):
            continue
        # “不要…”很短时可能是命令；至少要求有一个对象/动作
        if clause.startswith(("不要", "别")) and len(clause) < 4:
            continue
        if clause not in out:
            out.append(clause)
        if len(out) >= 2:
            break
    return out

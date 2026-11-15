"""画像与推荐类工具。

它们依赖数据库和模板库，所以真正实现由 `ToolContext.services` 注入：
服务层把 `portrait_brief` / `recommend` 塞进来，工具模块自己不 import web，
单测里塞假实现即可。
"""
from __future__ import annotations

from analysis.agent.contracts import ToolContext, integer, obj
from analysis.agent.registry import tool


@tool(
    "get_profile",
    "用户的自述目标/偏好/舒适音区/历史倾向。用来少问一句，"
    "不能当成这次测量。用户问‘我适合唱什么’前先看它。",
)
def get_profile(ctx: ToolContext) -> dict:
    fn = (ctx.services or {}).get("portrait_brief")
    if not callable(fn):
        return {"available": False, "note": "没有画像服务（或这位还没有画像）。"}
    try:
        brief = fn(ctx.singer_id) or {}
    except Exception as e:  # noqa: BLE001 —— 画像挂了不能拖垮追问
        return {"available": False, "note": f"画像读取失败：{str(e)[:60]}"}
    if not brief:
        return {"available": False, "note": "这位还没有画像。"}
    return {"available": True, **brief,
            "note": "自述与历史倾向，不是本次测量；本次报告优先。"}


@tool(
    "get_recommendations",
    "按舒适音区和偏好推荐本机曲目。用户问‘接下来唱什么/换首歌’时用。",
    obj({"limit": integer("推荐几条，1–4", 1, 4)}, []),
)
def get_recommendations(ctx: ToolContext, limit: int = 3) -> dict:
    fn = (ctx.services or {}).get("recommend")
    if not callable(fn):
        return {"available": False, "note": "没有推荐服务。"}
    try:
        rec = fn(ctx.singer_id, max(1, min(4, int(limit)))) or {}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "note": f"推荐失败：{str(e)[:60]}"}
    return {"available": True,
            "items": rec.get("items") or [],
            "reason": rec.get("reason") or "",
            "disclaimer": rec.get("disclaimer") or ""}

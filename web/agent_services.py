"""Agent 工具需要的服务：画像、推荐。

为什么放在 web 层：它们要读 SQLite 和模板库；`analysis/agent/` 保持纯函数，
工具层只认 `ToolContext.services` 里的可调用对象，单测可以塞假实现。

这里的两个函数是从 `web/server.py` 抽出来的（原来是 `_portrait_for` 与
`_recommend_candidates` 里的逻辑）——HTTP 端点和 MCP server 共用同一份，
避免"两处实现迟早分叉"。

缓存
----
`portrait_for()` 会读最多 120 条完整报告（每条几十到几百 KB），实测 ~112ms。
一次运行里模型可能先问 `get_profile` 再问 `get_recommendations`，
不缓存就是重复算两遍。`build()` 给一次运行发一个共享的惰性缓存，
**不跨请求**——画像随新练习变，跨请求缓存需要失效策略，不值得在这里做。
"""
from __future__ import annotations

from analysis import portrait as portrait_mod
from analysis import recommender
from analysis.align import BUILTIN, load_builtin
from web import practice_store, templates


def portrait_for(sid: str, limit: int = 500) -> dict:
    """从库里已有的练习报告聚合画像。

    为什么要 load_report：`sessions_of` 的行只有分数，`pitch.accuracy`、
    `rhythm.tempo_ratio` 这些在完整报告里；画像要"偏低多少/稳不稳"，
    不能只看一个音准分。只做只读聚合，不重新分析音频。
    """
    ss = practice_store.sessions_of(sid, min(500, max(1, int(limit))))
    reports = []
    for s in ss:
        rep = practice_store.load_report(s.get("session_uid")) or {}
        if not isinstance(rep, dict) or not rep:
            continue
        rep.setdefault("created_at", s.get("created_at"))
        rep.setdefault("reference_title", s.get("reference_title"))
        reports.append(rep)
    prof = practice_store.get_profile(sid)
    out = portrait_mod.build_portrait(reports, prof)
    out["singer_id"] = sid
    return out


def brief_for(sid: str, portrait: dict | None = None) -> dict:
    """给 Agent 的一小段画像背景（不是整份画像）。"""
    if not sid:
        return {}
    p = portrait if isinstance(portrait, dict) else portrait_for(sid, 120)
    return portrait_mod.portrait_brief(p)


def recommend_candidates(sid: str) -> list[dict]:
    """本机可推荐的曲目候选：内置曲目 + 用户模板（不含微练习）。"""
    candidates: list[dict] = []
    for rid in BUILTIN:
        try:
            ref = load_builtin(rid, first_start_ms=0)
            candidates.append({
                "kind": "builtin", "id": rid, "title": ref.title,
                "notes": [{"midi": n.pitch_midi} for n in ref.notes],
                "duration_ms": (ref.notes[-1].end_ms if ref.notes else 0),
            })
        except Exception:  # noqa: BLE001
            continue
    try:
        for t in templates.list_templates(sid, 50):
            title = str(t.get("title") or "").strip()
            if not title or title.startswith("微练习"):
                continue
            try:
                tpl = templates.load(t.get("template_id"))
            except Exception:  # noqa: BLE001
                tpl = None
            if not tpl or not tpl.notes:
                continue
            candidates.append({
                "kind": "template", "id": t.get("template_id"),
                "title": title,
                "source_kind": (t.get("source_kind") or tpl.source_kind or ""),
                "notes": [{"midi": n.get("pitch_midi")} for n in tpl.notes],
                "duration_ms": t.get("duration_ms"),
            })
    except Exception as e:  # noqa: BLE001
        print(f"[SingLens] ⚠️ 读取模板推荐失败：{e}")
    return candidates


def recommend_for(sid: str, limit: int = 3,
                  portrait: dict | None = None) -> dict:
    prof = practice_store.get_profile(sid)
    p = portrait if isinstance(portrait, dict) else portrait_for(sid, 120)
    comfort = p.get("comfort") or prof.get("comfort")
    stated = dict(prof)
    stated["top_song"] = (((p.get("observed") or {}).get("habit") or {})
                          .get("top_song") or "")
    return recommender.recommend(comfort, stated,
                                 recommend_candidates(sid),
                                 max_items=max(1, min(4, int(limit))))


def build(sid: str, uid: str = "") -> dict:
    """`ToolContext.services` 的内容。

    一次运行共享一份画像：`get_profile` + `get_recommendations` 都调时
    只聚合一次（实测 112ms → 只付一次）。
    """
    cache: dict[str, dict] = {}

    def _portrait() -> dict:
        if "p" not in cache:
            cache["p"] = portrait_for(sid, 120)
        return cache["p"]

    def _brief(sid_in: str) -> dict:
        return brief_for(sid_in, _portrait()) if sid_in else {}

    def _recommend(sid_in: str, limit: int = 3) -> dict:
        return recommend_for(sid_in, limit, _portrait())

    return {"portrait_brief": _brief, "recommend": _recommend}

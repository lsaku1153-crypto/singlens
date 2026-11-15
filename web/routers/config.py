"""配置与 LLM key 端点。

从 `web/server.py` 拆出的第二组。这些端点只碰 `CONFIG`、`config_store`
和 `coach`，不碰会话/音频管线。

为什么用 `_srv()` 延迟取 server：`server.py` 是组合根，要在它定义完
`CONFIG` / `fail` / `_json` 之后才能 import 这些 router；模块级互相 import
会形成环。请求到达时 server 已经完整加载，这一层延迟是安全的。
"""
from __future__ import annotations

import math
import os
from typing import Optional

from fastapi import APIRouter, Request

from web import config_store

router = APIRouter()


def _srv():
    from web import server
    return server


@router.get("/api/config")
async def get_config():
    srv = _srv()
    srv._refresh_privacy()
    return srv.ok(srv.CONFIG)


@router.put("/api/config")
async def put_config(req: Request):
    """按【组】更新。可以只传某一组里的某几个键：
         {"feedback": {"show_jargon": true}}

    ⚠️ 三条不能退的约束（2026-09-23 审计修过一次永久性故障）：
      ① **先全量校验、再原子替换** —— 校验失败时 CONFIG 一个字节都不动；
         `json.loads` 接受 NaN/Infinity，写进去后每次 GET 都会 500。
      ② 拒绝非有限浮点与类型不符的值。
      ③ 组名/键名不在 CONFIG 里就报错（静默忽略会让前端以为存上了）。
    """
    srv = _srv()
    deny = srv._remote_operator_denied(req)
    if deny:
        return deny
    body = await srv._json(req)
    if not isinstance(body, dict):
        return srv.fail("BAD_REQUEST",
                        "请求体必须是一个对象，形如 {\"feedback\": {\"show_jargon\": true}}。")
    # `_json()` 会给普通账号补一个顶层 singer_id；它不是配置组，先拿掉。
    body = {k: v for k, v in body.items() if k != "singer_id"}

    # 普通 singer 可以调体验类配置，但不能改隐私组；管理员不受白名单限制。
    user = getattr(req.state, "user", None)
    if user and user.get("role") != "admin":
        allowed = {"feedback", "recording", "range"}
        denied = [g for g in body if g not in allowed]
        if denied:
            return srv.fail("FORBIDDEN",
                            f"普通账号不能改这些配置组：{', '.join(denied)}。", 403)

    def bad(v) -> Optional[str]:
        if isinstance(v, bool) or v is None:
            return None
        if isinstance(v, (int, float)):
            if not math.isfinite(float(v)):
                return f"{v!r} 不是有限数值"
            return None
        if isinstance(v, str):
            return None
        return f"不支持的类型 {type(v).__name__}"

    # ── ① 先全量校验（不动 CONFIG）──
    staged, changed = {}, {}
    for grp, patch in body.items():
        if grp not in srv.CONFIG:
            return srv.fail("BAD_REQUEST", f"没有「{grp}」这一组设置。"
                                           f"可用的组：{', '.join(sorted(srv.CONFIG))}")
        if not isinstance(patch, dict):
            return srv.fail("BAD_REQUEST", f"「{grp}」的值必须是一个对象。")
        staged[grp] = {}
        for k, v in patch.items():
            if k not in srv.CONFIG[grp]:
                return srv.fail("BAD_REQUEST", f"「{grp}」里没有「{k}」这一项。"
                                               f"可用的键：{', '.join(sorted(srv.CONFIG[grp]))}")
            why = bad(v)
            if why is not None:
                return srv.fail("BAD_REQUEST", f"「{grp}.{k}」的值不合法：{why}。")
            cur = srv.CONFIG[grp][k]
            if isinstance(cur, bool) and not isinstance(v, bool):
                return srv.fail("BAD_REQUEST", f"「{grp}.{k}」应该是 true/false。")
            if isinstance(cur, (int, float)) and not isinstance(cur, bool) \
                    and not isinstance(v, (int, float)):
                return srv.fail("BAD_REQUEST", f"「{grp}.{k}」应该是数值。")
            staged[grp][k] = v
            changed[f"{grp}.{k}"] = v

    # ── ② 校验全过了才原子替换 ──
    for grp, kv in staged.items():
        srv.CONFIG[grp].update(kv)
    # ③ 落盘（失败只影响"下次启动记不住"，本次仍生效）
    if staged and not config_store.save(srv.CONFIG):
        print("[SingLens] ⚠️ 设置写入 data/config.json 失败（本次仍生效）")
    return srv.ok({"updated": changed, "config": srv.CONFIG})


# ══════════════════════════════════════════════════════════════
# LLM API key 的前端管理（2026-10-07）
#
#   ⚠️ 三条安全约束（改动这里之前先读）：
#     ① **key 永不回显** —— GET 只返回 `configured` / `hint`（前3+后4）/ `source`
#     ② 保存前**先真调一次**验证（`test_key`）
#     ③ 文件在 `.gitignore` 里（`*.key` / `*llm_key*`），绝不进版本库
# ══════════════════════════════════════════════════════════════
@router.get("/api/llm/key")
async def llm_key_status():
    """查询 LLM key 的**状态**（不回显 key 本身）。"""
    srv = _srv()
    from analysis import coach as _c
    src = _c.key_source()
    return srv.ok({
        "configured": src != "none",
        "source": src,
        "hint": _c.key_hint(),
        "base": (os.environ.get("SINGLENS_LLM_BASE") or _c.DEFAULT_BASE),
        "model": (os.environ.get("SINGLENS_LLM_MODEL") or _c.DEFAULT_MODEL),
        "enabled": bool(srv.CONFIG.get("feedback", {}).get("ai_comment")),
    })


@router.post("/api/llm/key")
async def llm_key_save(req: Request):
    """保存 key，并**立刻真调一次**验证。

    请求体：`{"key": "sk-...", "enable": true}`；`enable` 省略时默认 true。
    """
    srv = _srv()
    from analysis import coach as _c
    body = await srv._json(req)
    if not isinstance(body, dict):
        return srv.fail("BAD_REQUEST",
                        "请求体必须是一个对象，形如 {\"key\": \"sk-...\"}。")
    key = body.get("key")
    if not isinstance(key, str) or not key.strip():
        return srv.fail("BAD_REQUEST", "缺 key，或 key 不是字符串。")
    key = key.strip()
    if len(key) < 12:
        return srv.fail("BAD_REQUEST", "这把 key 太短了，看着不像真的。")
    if any(ch in key for ch in " \t\r\n"):
        return srv.fail("BAD_REQUEST", "key 里有空白字符，多半是复制时带进来的 —— "
                                       "请重新复制。")

    # ★ 先验证再保存：验证不通过就不落盘，避免"存了一把不能用的"
    good, why = _c.test_key(key)
    if not good:
        return srv.fail("BAD_REQUEST", f"这把 key 用不了：{why}")
    if not _c.save_key(key):
        return srv.fail("SERVER_ERROR", "key 写不进文件（检查 data/ 目录权限）。")

    # 配了 key 就默认打开开关 —— 否则用户会以为"配了但没生效"
    if body.get("enable", True):
        srv.CONFIG.setdefault("feedback", {})["ai_comment"] = True
        config_store.save(srv.CONFIG)
    print(f"[SingLens] LLM key 已保存（{_c.key_source()}，"
          f"{_c.key_hint()}），AI 点评 = "
          f"{srv.CONFIG.get('feedback', {}).get('ai_comment')}")
    return srv.ok({"saved": True, "verified": True, "hint": _c.key_hint(),
                   "enabled": bool(srv.CONFIG.get("feedback", {}).get("ai_comment"))})


@router.post("/api/llm/key/test")
async def llm_key_test(req: Request):
    """测试当前 key 能不能用（不保存）；也接受 `{"key": "..."}` 测未保存的。"""
    srv = _srv()
    from analysis import coach as _c
    try:
        body = await srv._json(req)
    except Exception:  # noqa: BLE001
        body = {}
    k = body.get("key") if isinstance(body, dict) else None
    k = k.strip() if isinstance(k, str) and k.strip() else None
    good, why = _c.test_key(k)
    return srv.ok({"ok": good, "reason": why,
                   "hint": _c.key_hint() if k is None else None})


@router.delete("/api/llm/key")
async def llm_key_clear():
    """清掉保存的 key（**环境变量那份清不掉**，会如实说明）。"""
    srv = _srv()
    from analysis import coach as _c
    src = _c.key_source()
    if src == "env":
        return srv.fail("BAD_REQUEST",
                        "这把 key 来自环境变量 SINGLENS_LLM_KEY，"
                        "程序改不了它 —— 请在启动服务前 unset，或改用它启动。")
    removed = _c.clear_key()
    srv.CONFIG.setdefault("feedback", {})["ai_comment"] = False
    config_store.save(srv.CONFIG)
    return srv.ok({"cleared": removed, "enabled": False})

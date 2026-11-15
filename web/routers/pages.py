"""页面路由（HTML/图标/静态入口）。

从 `web/server.py` 拆出来的第一组。为什么先拆它：
这些端点只依赖 `WEB_DIR`、`FileResponse` 和 `auth_mod`，不碰 `SESSIONS` /
分析管线 —— 搬走之后回归最容易判断"是不是纯粹搬家"。

服务入口仍按原来的路径注册，行为不变：
`/`、`/login`、`/agent`、`/collect`、`/rate`、`/experiment`、`/selftest.html`、
`/apitest.html`、`/apitest`、`/favicon.svg`、`/favicon.ico`。
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import (FileResponse, JSONResponse, RedirectResponse,
                               Response)
from web import auth as auth_mod

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WEB_DIR = os.path.join(ROOT, "web")

router = APIRouter()

_FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<rect width="64" height="64" rx="10" fill="#c93a2a"/>'
    '<text x="32" y="45" font-family="Outfit,Helvetica,Arial,sans-serif" '
    'font-size="42" font-weight="800" fill="#ffffff" text-anchor="middle">S</text>'
    '</svg>'
)


def _ok(data=None):
    return {"ok": True, "data": data}


@router.get("/favicon.svg")
async def favicon_svg():
    return Response(content=_FAVICON, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/favicon.ico")
async def favicon_ico():
    """兜底：老浏览器/没声明 icon 的页面仍会来要 `.ico`，给同一份 SVG。

    ⚠️ Content-Type 是 image/svg+xml（不是 image/x-icon）—— 这是**故意的**：
       Chrome/Edge/Firefox 都按内容嗅探，SVG 能正常显示；
       而如果这里返回 404，控制台就会一直挂一条红色报错（见 server.py 的注释）。
    """
    return Response(content=_FAVICON, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/selftest.html")
async def selftest_page():
    """契约自检页（前端侧 71 项）。"""
    return FileResponse(os.path.join(WEB_DIR, "selftest.html"))


@router.get("/apitest.html")
async def apitest_page():
    """适配层测试页（真浏览器打真实后端）。"""
    return FileResponse(os.path.join(WEB_DIR, "apitest.html"))


@router.get("/agent")
async def agent_page():
    """Agent 控制台（T4）：独立页面，只读某个会话的报告来对话。

    不在这里做鉴权：页面本身是静态 HTML，真正的数据在 `/agent/chat`，
    由 API 层按会话归属校验（`_assert_session_owner`）。
    """
    return FileResponse(os.path.join(WEB_DIR, "agent.html"))


@router.get("/collect")
async def collect_page(request: Request, token: str = ""):
    """录音采集页（实验用）。

    ★ 带 `?token=<操作者令牌>` 打开时，顺手种一个 cookie（`sl_op`）。
      采集页里的 fetch 是**页面自己**发的，要求每个请求手动带 header
      会改一堆调用点；种了 cookie 之后"一次带令牌打开、后续自动带上"。
      （不是登录态，本服务没有账号。）
    """
    resp = FileResponse(os.path.join(WEB_DIR, "collect.html"))
    want = ""
    try:
        from web.experiment import DISABLE_TOKEN, operator_token
        want = "" if DISABLE_TOKEN else operator_token()
    except Exception:  # noqa: BLE001
        want = ""
    if want and token == want:
        resp.set_cookie("sl_op", token, httponly=True, samesite="strict",
                        max_age=30 * 24 * 3600)
    return resp


@router.get("/rate")
async def rate_page():
    """盲评页（实验用）。"""
    return FileResponse(os.path.join(WEB_DIR, "rate.html"))


@router.get("/experiment")
async def experiment_page():
    """采集进度页：直接返回 JSON，浏览器看即可。"""
    from web.experiment import connect, progress
    con = connect()
    try:
        return _ok(progress(con))
    finally:
        con.close()


@router.get("/apitest")
async def apitest():
    """apiSource 适配层测试页（真浏览器里打真实后端）。"""
    return FileResponse(os.path.join(WEB_DIR, "apitest.html"))


@router.get("/login")
async def login_page(request: Request):
    """产品登录页。

    已登录访问 /login 直接回首页；未登录才显示。登录页本身不加载 SPA，
    避免在未认证时把整套应用 JS 和配置请求先跑一遍。
    """
    if not auth_mod.auth_enabled():
        return RedirectResponse("/", status_code=302)
    if auth_mod.user_from_request(request):
        return RedirectResponse("/", status_code=302)
    p = os.path.join(WEB_DIR, "login.html")
    if not os.path.exists(p):
        return JSONResponse({"ok": False, "error": {
            "code": "NOT_FOUND", "message": "登录页文件不存在。"}}, status_code=500)
    return FileResponse(p, headers={"Cache-Control": "no-store"})


@router.get("/")
async def index(request: Request):
    """产品入口。

    `index.html` 不存在时退回自检页，不至于白屏；登录开启时未登录先回 `/login`。
    """
    if auth_mod.auth_enabled() and not auth_mod.user_from_request(request):
        return RedirectResponse("/login", status_code=302)
    for name in ("index.html", "selftest.html"):
        p = os.path.join(WEB_DIR, name)
        if os.path.exists(p):
            return FileResponse(p)
    return JSONResponse(
        {"ok": True, "data": {"service": "SingLens API", "docs": "/docs"}})

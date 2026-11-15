"""登录 / 注册 / 改密端点。

从 `web/server.py` 拆出的第三组。安全约束原样保留：
loopback 注册、同源校验、失败限流、cookie SameSite=Lax + HttpOnly。

`_srv()` 延迟取 server：组合根要先定义 `fail`/`_json`/`_auth_same_origin`
与 `practice_store`，模块级互相 import 会成环。
"""
from __future__ import annotations

import sqlite3
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from web import auth as auth_mod

router = APIRouter()


def _srv():
    from web import server
    return server


@router.get("/api/auth/me")
async def auth_me(request: Request):
    srv = _srv()
    if not auth_mod.auth_enabled():
        return srv.ok({"auth_enabled": False, "user": {
            "user_id": "local", "username": "local", "role": "admin",
            "singer_id": None, "display_name": "本机", "must_change_password": False}})
    user = auth_mod.user_from_request(request)
    if not user:
        return srv.fail("UNAUTHORIZED", "请先登录。", 401)
    return srv.ok({"auth_enabled": True, "user": user})


@router.post("/api/auth/login")
async def auth_login(request: Request):
    srv = _srv()
    if not auth_mod.auth_enabled():
        return srv.fail("CONFLICT", "当前服务未启用登录。", 409)
    if not srv._auth_same_origin(request):
        return srv.fail("FORBIDDEN", "拒绝了跨站登录请求。", 403)
    body = await srv._json(request)
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    remember = bool(body.get("remember"))
    ip = (request.client.host if request.client else "") or ""
    key = auth_mod.rate_key(ip, username)
    if auth_mod.attempts_left(key) <= 0:
        return srv.fail("TOO_MANY", "尝试次数过多，请 10 分钟后再试。", 429)
    user = auth_mod.authenticate(username, password)
    if not user:
        auth_mod.record_failure(key)
        return srv.fail("UNAUTHORIZED", "账号或密码对不上，再试一次。", 401)
    auth_mod.clear_failures(key)
    ttl = auth_mod.REMEMBER_TTL if remember else auth_mod.SESSION_TTL
    token = auth_mod.create_session(
        user["user_id"], ttl=ttl, ip=ip,
        user_agent=request.headers.get("user-agent") or "")
    resp = JSONResponse({"ok": True, "data": {"user": user}})
    resp.set_cookie(
        auth_mod.COOKIE_NAME, token, max_age=ttl,
        httponly=True, samesite="lax", secure=auth_mod.cookie_secure(), path="/")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.get("/api/auth/register")
async def auth_register_status(request: Request):
    """登录页用它决定是否显示“创建练习账号”。

    默认只允许回环地址注册；局域网开放必须显式设 SINGLENS_REGISTER=open。
    """
    srv = _srv()
    if not auth_mod.auth_enabled():
        return srv.ok({"allowed": False, "mode": "closed", "reason": "登录未启用。"})
    ip = (request.client.host if request.client else "") or ""
    allowed = auth_mod.register_allowed(ip)
    reason = ("本机可以创建练习账号。"
              if allowed else "注册只在本机开放；局域网账号由管理员发放。")
    return srv.ok({"allowed": allowed, "mode": auth_mod.register_mode(),
                   "reason": reason})


@router.post("/api/auth/register")
async def auth_register(request: Request):
    """创建“演唱者 + 账号”并直接登录。

    ⚠️ 默认 loopback：只有本机能注册。局域网要开放，必须显式设置
       `SINGLENS_REGISTER=open`，并且自己承担同网段开号的风险。
    """
    srv = _srv()
    if not auth_mod.auth_enabled():
        return srv.fail("CONFLICT", "当前服务未启用登录。", 409)
    ip = (request.client.host if request.client else "") or ""
    if not auth_mod.register_allowed(ip):
        return srv.fail("FORBIDDEN", "注册只在本机开放；局域网账号由管理员发放。", 403)
    if not srv._auth_same_origin(request):
        return srv.fail("FORBIDDEN", "拒绝了跨站注册请求。", 403)
    body = await srv._json(request)
    name = str(body.get("name") or "").strip()
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    confirm = str(body.get("confirm") or "")
    if not name:
        return srv.fail("BAD_REQUEST", "先给自己起一个练习名字。", 400)
    if len(name) > srv.SINGER_NAME_MAX:
        return srv.fail("BAD_REQUEST",
                        f"名字太长了（{len(name)} > {srv.SINGER_NAME_MAX} 字）。", 400)
    if not auth_mod.USERNAME_RE.match(username):
        return srv.fail("BAD_REQUEST", "账号只允许字母/数字/下划线/点/短横线，3–32 位。", 400)
    if len(password) < auth_mod.MIN_PASSWORD:
        return srv.fail("BAD_REQUEST", f"密码至少 {auth_mod.MIN_PASSWORD} 位。", 400)
    if confirm and password != confirm:
        return srv.fail("BAD_REQUEST", "两次输入的密码不一致。", 400)
    if auth_mod.get_user_by_username(username):
        return srv.fail("CONFLICT", "这个账号已经被占用了。", 409)
    sid = f"u_{uuid.uuid4().hex[:8]}"
    if srv.practice_store.name_taken(name, sid):
        return srv.fail("CONFLICT", f"已经有一位练习者叫「{name}」了。", 409)
    created_singer = False
    try:
        srv.practice_store.upsert_singer(sid, name)
        created_singer = True
        user = auth_mod.create_user(
            username=username, password=password, role="singer",
            singer_id=sid, display_name=name)
    except (ValueError, sqlite3.IntegrityError) as e:
        if created_singer:
            try:
                srv.practice_store.delete_singer(sid)
            except Exception:  # noqa: BLE001
                pass
        return srv.fail("CONFLICT", f"创建账号失败：{e}", 409)
    except Exception as e:  # noqa: BLE001
        if created_singer:
            try:
                srv.practice_store.delete_singer(sid)
            except Exception:  # noqa: BLE001
                pass
        return srv.fail("INTERNAL", f"创建账号失败：{e}", 500)
    token = auth_mod.create_session(
        user["user_id"], ttl=auth_mod.SESSION_TTL, ip=ip,
        user_agent=request.headers.get("user-agent") or "")
    resp = JSONResponse({"ok": True, "data": {"user": user}})
    resp.set_cookie(
        auth_mod.COOKIE_NAME, token, max_age=auth_mod.SESSION_TTL,
        httponly=True, samesite="lax", secure=auth_mod.cookie_secure(), path="/")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.post("/api/auth/logout")
async def auth_logout(request: Request):
    token = auth_mod.token_from_cookie_header(request.headers.get("cookie", ""))
    auth_mod.revoke_session(token)
    resp = JSONResponse({"ok": True, "data": {"logged_out": True}})
    resp.delete_cookie(auth_mod.COOKIE_NAME, path="/")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.post("/api/auth/password")
async def auth_password(request: Request):
    srv = _srv()
    if not auth_mod.auth_enabled():
        return srv.fail("CONFLICT", "当前服务未启用登录。", 409)
    user = auth_mod.user_from_request(request)
    if not user:
        return srv.fail("UNAUTHORIZED", "请先登录。", 401)
    body = await srv._json(request)
    current = str(body.get("current_password") or "")
    new = str(body.get("new_password") or "")
    full = auth_mod.get_user_by_id(user["user_id"])
    if not full or not auth_mod.verify_password(current, full["password_hash"]):
        return srv.fail("UNAUTHORIZED", "当前密码不正确。", 401)
    try:
        auth_mod.set_password(user["user_id"], new)
    except ValueError as e:
        return srv.fail("BAD_REQUEST", str(e), 400)
    auth_mod.revoke_user_sessions(user["user_id"])
    resp = JSONResponse({"ok": True, "data": {
        "changed": True, "message": "密码已更新，请重新登录。"}})
    resp.delete_cookie(auth_mod.COOKIE_NAME, path="/")
    return resp

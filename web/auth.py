"""本地账号与会话。

设计边界（2026-10-28，见 docs/接口契约）：
  · 这是**单机/小范围**的轻量账号，不是互联网多租户；
  · 一个 singer 账号绑定一个 app_singer；admin 账号可切换；
  · 密码只存 scrypt 派生值；会话只存 token 的 SHA-256；
  · 研究采集 / 盲评继续走操作者令牌，不进入这套登录。

为什么密码不引第三方依赖：
  这个项目的运行环境是 Windows + venv，很多机器没有 passlib/argon2；
  `hashlib.scrypt` 是标准库，n=2^14/r=8/p=1 对毕设单机足够。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
import uuid
from typing import Optional

from web import practice_store

COOKIE_NAME = "sl_session"
SESSION_TTL = 12 * 3600
REMEMBER_TTL = 30 * 24 * 3600
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PASSWORD = 10
MAX_PASSWORD = 128

# 登录失败窗口。单进程内存即可；重启清零不影响安全。
_ATTEMPTS: dict[str, list[float]] = {}


def auth_enabled() -> bool:
    """默认开启。测试/旧自动化用 SINGLENS_AUTH=0 显式关闭。"""
    return os.environ.get("SINGLENS_AUTH", "1").strip().lower() not in (
        "0", "false", "no", "off"
    )


def cookie_secure() -> bool:
    return os.environ.get("SINGLENS_COOKIE_SECURE", "0").strip().lower() in (
        "1", "true", "yes", "on"
    )


def register_mode() -> str:
    """注册范围。默认 loopback：只有坐在本机的人能注册。

    open / loopback / closed。局域网要开放注册必须显式改环境变量；
    否则同网段任何人都能建练习身份，和“账号由管理员发放”的默认相反。
    """
    return os.environ.get("SINGLENS_REGISTER", "loopback").strip().lower()


def register_allowed(ip: str) -> bool:
    mode = register_mode()
    if mode in ("0", "false", "no", "off", "closed"):
        return False
    if mode == "open":
        return True
    if mode in ("loopback", "local", "localhost"):
        return (ip or "").split("%", 1)[0] in ("127.0.0.1", "::1", "localhost")
    return False


def _connect() -> sqlite3.Connection:
    con = practice_store.connect()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS app_user (
            user_id             TEXT PRIMARY KEY,
            username            TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash       TEXT NOT NULL,
            role                TEXT NOT NULL DEFAULT 'singer'
                                  CHECK(role IN ('singer','admin')),
            singer_id           TEXT UNIQUE,
            display_name        TEXT,
            disabled            INTEGER NOT NULL DEFAULT 0,
            must_change_password INTEGER NOT NULL DEFAULT 0,
            created_at          REAL NOT NULL,
            last_login_at       REAL
        );
        CREATE INDEX IF NOT EXISTS idx_app_user_singer
            ON app_user(singer_id);
        CREATE TABLE IF NOT EXISTS auth_session (
            token_hash          TEXT PRIMARY KEY,
            user_id             TEXT NOT NULL,
            created_at          REAL NOT NULL,
            last_seen_at        REAL NOT NULL,
            expires_at          REAL NOT NULL,
            ip                  TEXT,
            user_agent          TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_auth_session_user
            ON auth_session(user_id);
        CREATE INDEX IF NOT EXISTS idx_auth_session_exp
            ON auth_session(expires_at);
    """)
    con.commit()
    return con


def init_db() -> None:
    con = _connect()
    con.close()


def _b64(data: bytes) -> str:
    # 不用 base64 也能工作；这里用 hex 只为让 CLI/日志里不出现二进制。
    return data.hex()


def hash_password(password: str) -> str:
    if not isinstance(password, str):
        raise ValueError("密码必须是字符串")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN,
    )
    return (f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}$"
            f"{salt.hex()}${digest.hex()}")


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, digest_hex = (stored or "").split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(
            (password or "").encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p),
            dklen=len(bytes.fromhex(digest_hex)),
        )
        return hmac.compare_digest(got.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _public(row: sqlite3.Row | dict) -> dict:
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "role": row["role"],
        "singer_id": row["singer_id"],
        "display_name": row["display_name"] or row["username"],
        "must_change_password": bool(row["must_change_password"]),
    }


def list_users() -> list[dict]:
    con = _connect()
    try:
        rows = con.execute(
            "SELECT * FROM app_user ORDER BY role DESC, created_at ASC"
        ).fetchall()
        return [_public(r) for r in rows]
    finally:
        con.close()


def get_user_by_id(user_id: str) -> Optional[dict]:
    con = _connect()
    try:
        row = con.execute("SELECT * FROM app_user WHERE user_id=?",
                          (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def get_user_by_username(username: str) -> Optional[dict]:
    con = _connect()
    try:
        row = con.execute("SELECT * FROM app_user WHERE username=?",
                          (username,)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def create_user(*, username: str, password: str, role: str = "singer",
                singer_id: Optional[str] = None,
                display_name: str = "",
                must_change_password: bool = False) -> dict:
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise ValueError("账号只允许字母/数字/下划线/点/短横线，3–32 位。")
    if len(password or "") < MIN_PASSWORD:
        raise ValueError(f"密码至少 {MIN_PASSWORD} 位。")
    if len(password) > MAX_PASSWORD:
        raise ValueError(f"密码不能超过 {MAX_PASSWORD} 位。")
    if role not in ("singer", "admin"):
        raise ValueError("role 只能是 singer 或 admin。")
    if role == "singer":
        if not singer_id:
            raise ValueError("singer 账号必须绑定 singer_id。")
        if practice_store.get_singer(singer_id) is None:
            raise ValueError(f"app_singer 里没有 {singer_id}。")
    else:
        if singer_id:
            raise ValueError("admin 账号不能绑定 singer_id。")
    con = _connect()
    try:
        uid = uuid.uuid4().hex
        con.execute(
            "INSERT INTO app_user (user_id, username, password_hash, role,"
            " singer_id, display_name, disabled, must_change_password,"
            " created_at) VALUES (?,?,?,?,?,?,0,?,?)",
            (uid, username, hash_password(password), role,
             singer_id, (display_name or username).strip(),
             1 if must_change_password else 0, time.time()),
        )
        con.commit()
        row = con.execute("SELECT * FROM app_user WHERE user_id=?",
                          (uid,)).fetchone()
        return _public(row)
    except sqlite3.IntegrityError as e:
        raise ValueError(f"账号或演唱者已被占用：{e}") from e
    finally:
        con.close()


def set_password(user_id: str, password: str,
                 must_change: bool = False) -> bool:
    if len(password or "") < MIN_PASSWORD:
        raise ValueError(f"密码至少 {MIN_PASSWORD} 位。")
    if len(password) > MAX_PASSWORD:
        raise ValueError(f"密码不能超过 {MAX_PASSWORD} 位。")
    con = _connect()
    try:
        cur = con.execute(
            "UPDATE app_user SET password_hash=?, must_change_password=?"
            " WHERE user_id=?",
            (hash_password(password), 1 if must_change else 0, user_id),
        )
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def set_disabled(user_id: str, disabled: bool) -> bool:
    con = _connect()
    try:
        cur = con.execute("UPDATE app_user SET disabled=? WHERE user_id=?",
                          (1 if disabled else 0, user_id))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def delete_user(user_id: str) -> bool:
    con = _connect()
    try:
        con.execute("DELETE FROM auth_session WHERE user_id=?", (user_id,))
        cur = con.execute("DELETE FROM app_user WHERE user_id=?", (user_id,))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def authenticate(username: str, password: str) -> Optional[dict]:
    row = get_user_by_username((username or "").strip())
    if not row or row.get("disabled"):
        return None
    if not verify_password(password or "", row["password_hash"]):
        return None
    con = _connect()
    try:
        con.execute("UPDATE app_user SET last_login_at=? WHERE user_id=?",
                    (time.time(), row["user_id"]))
        con.commit()
    finally:
        con.close()
    return _public(row)


def create_session(user_id: str, *, ttl: int = SESSION_TTL,
                   ip: str = "", user_agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    con = _connect()
    try:
        con.execute(
            "INSERT INTO auth_session (token_hash, user_id, created_at,"
            " last_seen_at, expires_at, ip, user_agent)"
            " VALUES (?,?,?,?,?,?,?)",
            (hashlib.sha256(token.encode()).hexdigest(), user_id,
             now, now, now + max(60, int(ttl)), ip[:128], user_agent[:300]),
        )
        con.commit()
        return token
    finally:
        con.close()


def get_session_user(token: str) -> Optional[dict]:
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    con = _connect()
    try:
        row = con.execute(
            "SELECT s.token_hash, s.expires_at, s.last_seen_at, u.*"
            " FROM auth_session s JOIN app_user u ON u.user_id=s.user_id"
            " WHERE s.token_hash=?",
            (token_hash,),
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] <= time.time() or row["disabled"]:
            con.execute("DELETE FROM auth_session WHERE token_hash=?", (token_hash,))
            con.commit()
            return None
        if time.time() - (row["last_seen_at"] or 0) > 60:
            con.execute("UPDATE auth_session SET last_seen_at=? WHERE token_hash=?",
                        (time.time(), token_hash))
            con.commit()
        return _public(row)
    finally:
        con.close()


def revoke_session(token: str) -> None:
    if not token:
        return
    con = _connect()
    try:
        con.execute("DELETE FROM auth_session WHERE token_hash=?",
                    (hashlib.sha256(token.encode()).hexdigest(),))
        con.commit()
    finally:
        con.close()


def revoke_user_sessions(user_id: str) -> None:
    con = _connect()
    try:
        con.execute("DELETE FROM auth_session WHERE user_id=?", (user_id,))
        con.commit()
    finally:
        con.close()


def cleanup_sessions() -> int:
    con = _connect()
    try:
        cur = con.execute("DELETE FROM auth_session WHERE expires_at <= ?",
                          (time.time(),))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def token_from_cookie_header(cookie_header: str) -> str:
    for part in (cookie_header or "").split(";"):
        if "=" not in part:
            continue
        k, v = part.strip().split("=", 1)
        if k == COOKIE_NAME:
            return v.strip()
    return ""


def user_from_request(request) -> Optional[dict]:
    """只依赖 Request.headers，不引入 FastAPI 依赖。"""
    token = token_from_cookie_header(request.headers.get("cookie", ""))
    return get_session_user(token) if token else None


def rate_key(ip: str, username: str) -> str:
    return f"{ip}|{(username or '').strip().lower()}"


def attempts_left(key: str, *, window: int = 600, limit: int = 5) -> int:
    now = time.time()
    arr = [t for t in _ATTEMPTS.get(key, []) if now - t < window]
    _ATTEMPTS[key] = arr
    return max(0, limit - len(arr))


def record_failure(key: str) -> None:
    _ATTEMPTS.setdefault(key, []).append(time.time())


def clear_failures(key: str) -> None:
    _ATTEMPTS.pop(key, None)

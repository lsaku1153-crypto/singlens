"""OpenAI 兼容的最小客户端。

为什么不用官方 SDK：这个项目从第一天就是标准库直连，换 base_url 就能
换模型/网关，不引额外依赖。

连接复用（2026-11-14）
--------------------
以前每次 `urllib.request.urlopen` 都新建 TCP + TLS；`urllib` 还会主动发
`Connection: close`，所以 3 步循环 = 3 次握手。现在改成 `http.client` +
**线程本地连接池**：一次运行在同一个工作线程里跑，3 步复用同一条连接；
连接被对端关掉（`RemoteDisconnected`）时自动重连一次。

流式（2026-11-14）
-----------------
`chat_stream()` 把 content 增量通过 `on_delta` 回调吐出来（给 SSE 用），
返回值与非流式**同形状**，所以 `loop` 不需要为两种模式写两套解析。
`stream_options.include_usage` 用来在流末尾拿 usage（拿不到也不报错）；
网关不认这个字段（400）时会自动去掉重试一次。

`thinking` 字段只有 DeepSeek 官方域认；兼容网关收到可能 400，所以按域判断，
另外留 `SINGLENS_LLM_THINKING=0` 强制关掉。
"""
from __future__ import annotations

import http.client
import io
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse

DEFAULT_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"

_TLS = ssl.create_default_context()
_LOCAL = threading.local()
_STALE = (http.client.RemoteDisconnected, http.client.CannotSendRequest,
          http.client.ResponseNotReady, BrokenPipeError,
          ConnectionResetError, ConnectionAbortedError)


def endpoint() -> tuple[str, str]:
    base = (os.environ.get("SINGLENS_LLM_BASE") or DEFAULT_BASE).rstrip("/")
    model = os.environ.get("SINGLENS_LLM_MODEL") or DEFAULT_MODEL
    return base, model


def thinking_supported(base: str) -> bool:
    flag = (os.environ.get("SINGLENS_LLM_THINKING") or "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    return "deepseek.com" in base


def _retryable(err: Exception) -> bool:
    """只重试"可能下一次就好"的错误：网络、超时、5xx、429。

    401/403/400 重试没有意义（key 错、参数错），只会让用户多等一次。
    """
    if isinstance(err, urllib.error.HTTPError):
        return err.code >= 500 or err.code == 429
    return isinstance(err, (urllib.error.URLError, TimeoutError, OSError))


def _base_parts(base: str) -> tuple[str, str, int, str]:
    u = urllib.parse.urlsplit(base)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError(f"不支持的 LLM base：{base}")
    port = u.port or (443 if u.scheme == "https" else 80)
    return u.scheme, u.hostname, port, u.path.rstrip("/")


def _pool() -> dict:
    pool = getattr(_LOCAL, "pool", None)
    if pool is None:
        pool = _LOCAL.pool = {}
    return pool


def reset_pool() -> None:
    """关掉当前线程的连接池（测试 / 切换 base 用）。"""
    pool = getattr(_LOCAL, "pool", None)
    if not pool:
        return
    for conn in pool.values():
        try:
            conn.close()
        except OSError:
            pass
    pool.clear()


def _connect(scheme: str, host: str, port: int, timeout: float) -> http.client.HTTPConnection:
    pool = _pool()
    key = (scheme, host, port)
    conn = pool.get(key)
    if conn is not None and getattr(conn, "sock", None) is None:
        pool.pop(key, None)
        conn = None
    if conn is None:
        if scheme == "https":
            conn = http.client.HTTPSConnection(host, port, timeout=timeout,
                                               context=_TLS)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
        pool[key] = conn
    else:
        conn.timeout = timeout
        try:
            if conn.sock is not None:
                conn.sock.settimeout(timeout)
        except OSError:
            pass
    return conn


def _drop(key: tuple, conn) -> None:
    _pool().pop(key, None)
    try:
        conn.close()
    except OSError:
        pass


def _body(model: str, messages: list[dict], tools: list[dict] | None,
          tool_choice: str, thinking: bool, max_tokens: int,
          base: str) -> dict:
    body: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.4,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice
    if thinking_supported(base):
        body["thinking"] = {"type": "enabled" if thinking else "disabled"}
    return body


def _open(base: str, key: str, body: dict, timeout: float):
    """发一次 POST，返回 (conn, resp, pool_key, url)。

    复用的连接可能已经被对端关掉（keep-alive 超时）：这种错误在**拿到响应前**
    重连一次是安全的；拿到响应之后不再重试（调用方决定）。
    """
    scheme, host, port, prefix = _base_parts(base)
    url = f"{base.rstrip('/')}/chat/completions"
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
        "Accept": "text/event-stream" if body.get("stream") else "application/json",
        "User-Agent": "SingLens/1.0",
    }
    pool_key = (scheme, host, port)
    last: Exception | None = None
    for attempt in (0, 1):
        conn = _connect(scheme, host, port, timeout)
        try:
            conn.request("POST", prefix + "/chat/completions",
                         body=raw, headers=headers)
            resp = conn.getresponse()
        except _STALE as e:
            _drop(pool_key, conn)
            last = e
            if attempt == 0:
                continue
            raise
        except Exception:
            _drop(pool_key, conn)
            raise
        if resp.status >= 400:
            data = resp.read()
            if getattr(resp, "will_close", True):
                _drop(pool_key, conn)
            raise urllib.error.HTTPError(url, resp.status, resp.reason,
                                         resp.getheaders(), io.BytesIO(data))
        return conn, resp, pool_key, url
    raise last or RuntimeError("LLM 连接失败")


def _release(conn, resp, pool_key: tuple) -> None:
    """正常读完：能复用就留着；对端要关就丢掉。"""
    if getattr(resp, "will_close", True) or getattr(conn, "sock", None) is None:
        _drop(pool_key, conn)


def _post_json(base: str, key: str, body: dict, timeout: float) -> dict:
    conn, resp, pool_key, _url = _open(base, key, body, timeout)
    ok = False
    try:
        raw = resp.read()
        ok = True
    finally:
        if ok:
            _release(conn, resp, pool_key)
        else:
            _drop(pool_key, conn)
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise RuntimeError("LLM 返回的不是 JSON") from e


def chat(messages: list[dict], *, tools: list[dict] | None = None,
         tool_choice: str = "auto", thinking: bool = False,
         max_tokens: int = 1200, timeout: float = 20.0,
         key: str = "", retries: int = 0) -> dict:
    """一次非流式 `/chat/completions`。异常原样抛出，由 loop 决定降级。

    `retries=1` 时对网络类错误再试一次 —— 2026-11-12 的 live 评测里
    出现过一次 `llm_error` 直接把整轮退成规则版，那只是瞬时超时。
    """
    base, model = endpoint()
    body = _body(model, messages, tools, tool_choice, thinking, max_tokens, base)
    attempt = 0
    while True:
        try:
            return _post_json(base, key, body, timeout)
        except Exception as e:  # noqa: BLE001
            if attempt >= retries or not _retryable(e):
                raise
            attempt += 1
            time.sleep(0.4 * attempt)


def _error_text(err: urllib.error.HTTPError) -> str:
    try:
        raw = err.read()
        return raw.decode("utf-8", "replace")[:400]
    except Exception:  # noqa: BLE001
        return ""


def _do_stream(base: str, key: str, body: dict, timeout: float,
               on_delta, deadline_at: float | None,
               state: dict) -> dict:
    conn, resp, pool_key, _url = _open(base, key, body, timeout)
    ok = False
    saw_done = False
    parts: list[str] = []
    calls: dict[int, dict] = {}
    usage: dict = {}
    try:
        ctype = (resp.getheader("Content-Type") or "").lower()
        if "text/event-stream" not in ctype:
            # 网关无视 stream=true、直接回 JSON：按非流式处理，别把内容丢了。
            raw = resp.read()
            data = json.loads(raw.decode("utf-8"))
            ok = True
            return data
        for raw_line in resp:
            if deadline_at is not None and time.time() > deadline_at:
                raise TimeoutError("LLM 流超过运行预算")
            line = raw_line.decode("utf-8", "replace").strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                saw_done = True
                break
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            if isinstance(obj.get("usage"), dict) and obj["usage"]:
                usage = obj["usage"]
            for ch in obj.get("choices") or []:
                delta = ch.get("delta") or {}
                text = delta.get("content")
                if text:
                    parts.append(str(text))
                    state["emitted"] = True
                    if callable(on_delta):
                        try:
                            on_delta(str(text))
                        except Exception:  # noqa: BLE001 —— 前端断了不该中断生成
                            pass
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index")
                    idx = int(idx) if isinstance(idx, (int, float)) else 0
                    slot = calls.setdefault(idx, {
                        "id": "", "type": "function",
                        "function": {"name": "", "arguments": ""}})
                    if tc.get("id"):
                        slot["id"] = str(tc["id"])
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] = str(fn["name"])
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += str(fn["arguments"])
        if saw_done:
            # 关键：看到 [DONE] 只是"事件结束"，HTTP 响应体还有终止块。
            # 不把它读掉，连接状态会停在 REQ_SENT，下一次复用直接
            # `CannotSendRequest("Request-sent")`（2026-11-14 live 实测踩到）。
            # 给它 0.5s 读终止块；读不到就丢掉连接，绝不复用半截响应。
            try:
                if conn.sock is not None:
                    conn.sock.settimeout(0.5)
                resp.read()
            except Exception:  # noqa: BLE001 —— 读不到就 drop
                pass
            else:
                ok = True
        else:
            ok = True
    finally:
        if ok:
            _release(conn, resp, pool_key)
        else:
            _drop(pool_key, conn)
    msg: dict = {"role": "assistant", "content": "".join(parts)}
    if calls:
        msg["tool_calls"] = [calls[k] for k in sorted(calls)]
    return {"choices": [{"message": msg}], "usage": usage}


def chat_stream(messages: list[dict], *, tools: list[dict] | None = None,
                tool_choice: str = "auto", thinking: bool = False,
                max_tokens: int = 1200, timeout: float = 20.0,
                key: str = "", on_delta=None, deadline_at: float | None = None,
                retries: int = 0) -> dict:
    """流式 `/chat/completions`，返回值与非流式同形状。

    `on_delta(text)` 只在 content 增量上调用；tool_calls 只累积、不回调。
    已经吐出过内容后不再重试（重试会重复输出）。
    """
    base, model = endpoint()
    body = _body(model, messages, tools, tool_choice, thinking, max_tokens, base)
    body["stream"] = True
    body["stream_options"] = {"include_usage": True}
    state = {"emitted": False}
    attempt = 0
    while True:
        try:
            return _do_stream(base, key, body, timeout, on_delta,
                              deadline_at, state)
        except urllib.error.HTTPError as e:
            # 有些网关不认 stream_options：去掉它重试一次，别把整轮打挂。
            if e.code == 400 and "stream_options" in body \
                    and "stream_options" in _error_text(e):
                body.pop("stream_options", None)
                continue
            if attempt >= retries or not _retryable(e):
                raise
        except Exception as e:  # noqa: BLE001
            if state["emitted"] or attempt >= retries or not _retryable(e):
                raise
        attempt += 1
        time.sleep(0.4 * attempt)

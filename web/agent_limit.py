"""Agent 追问限流：按演唱者的窗口 + 进程内并发上限。

为什么需要：一次追问会串 2–4 次 LLM 调用，登录用户点快一点就能把额度烧光；
`/api/agent/chat` 之前没有任何配额。这里只做**本机单进程**的粗粒度保护——
会话/配置本来就在进程内存里（README「必须单进程」），做跨进程限流反而要外置状态。

边界：窗口是固定窗口（不是滑动日志），允许一次突发到上限；对本项目够用。
环境变量：
  SINGLENS_AGENT_WINDOW_S   窗口秒数，默认 600
  SINGLENS_AGENT_MAX_RUNS   每窗口最多追问次数，默认 30
  SINGLENS_AGENT_MAX_ACTIVE 同时最多在跑几次，默认 4
"""
from __future__ import annotations

import os
import threading
import time

WINDOW_S = float(os.environ.get("SINGLENS_AGENT_WINDOW_S") or 600)
MAX_RUNS = int(os.environ.get("SINGLENS_AGENT_MAX_RUNS") or 30)
MAX_ACTIVE = int(os.environ.get("SINGLENS_AGENT_MAX_ACTIVE") or 4)

_lock = threading.Lock()
_hits: dict[str, list[float]] = {}
_active = 0


def acquire(key: str) -> tuple[bool, str, int]:
    """申请一次运行。返回 `(ok, 拒绝原因, Retry-After 秒)`；成功即占用一个并发位。"""
    global _active
    now = time.time()
    with _lock:
        if _active >= MAX_ACTIVE:
            return False, "现在同时在跑的追问太多，稍等几秒再问。", 2
        arr = [t for t in _hits.get(key, []) if now - t < WINDOW_S]
        if len(arr) >= MAX_RUNS:
            _hits[key] = arr
            retry = max(1, int(WINDOW_S - (now - arr[0])) + 1)
            return False, (f"追问太频繁：{max(1, int(WINDOW_S // 60))} 分钟内"
                           f"最多 {MAX_RUNS} 次。"), retry
        arr.append(now)
        _hits[key] = arr
        _active += 1
    return True, "", 0


def release() -> None:
    """释放并发位。与 acquire 成对调用（成功才需要）。"""
    global _active
    with _lock:
        _active = max(0, _active - 1)


def reset() -> None:
    """清空计数（测试用）。"""
    global _active
    with _lock:
        _hits.clear()
        _active = 0


def snapshot() -> dict:
    with _lock:
        return {"window_s": WINDOW_S, "max_runs": MAX_RUNS,
                "max_active": MAX_ACTIVE, "active": _active}

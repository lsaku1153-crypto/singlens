"""工具执行器：校验参数 → 线程池执行 → 截断结果。

模型永远不该看到异常栈。三种失败都返回结构化 JSON：
  · `unknown_tool`      工具名不存在（附可用列表，模型能自己改）
  · `invalid_json`      参数不是合法 JSON
  · `invalid_arguments` 缺参数 / 类型错 / 越界 / 枚举不合法（附 schema）
  · `timeout` / `tool_failed`  执行层失败

成功返回 `{"ok": true, "data": ..., "ms": n}`。

参数宽松度
----------
整数位接受 `"2"` / `2.0` 这类等价写法（很多网关会这么给）。这不是放松校验：
值不等价（`"2abc"`、`2.5`）仍然拒绝；只是不再为了一个类型转换多烧一轮 LLM。

超时的边界
----------
`fut.result(timeout=...)` 只是**不再等**，线程本身会继续跑完（Python 不能强杀线程）。
工具都是毫秒级，所以影响可忽略；如果以后加慢工具，要自己在 handler 里查 deadline。
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FTimeout
from typing import Any

from analysis.agent import registry
from analysis.agent.contracts import ToolContext, ToolError

# 工具都是毫秒级内存计算，4 个线程够用；真正慢的是 LLM 调用，在 client 层超时。
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-tool")


def _coerce(schema: dict, args: dict) -> dict:
    """把等价的整数写法转成 int（"2" / 2.0 → 2）。

    只动 schema 声明为 integer 的字段；bool 是 int 的子类，必须显式排除。
    """
    props = schema.get("properties") or {}
    out = dict(args)
    for key, val in out.items():
        if (props.get(key) or {}).get("type") != "integer":
            continue
        if isinstance(val, bool):
            continue
        if isinstance(val, str):
            s = val.strip()
            try:
                out[key] = int(s)
                continue
            except ValueError:
                pass
            try:
                f = float(s)
            except ValueError:
                continue
            if f.is_integer():
                out[key] = int(f)
        elif isinstance(val, float) and val.is_integer():
            out[key] = int(val)
    return out


def _validate(schema: dict, args: dict) -> str:
    """返回空串表示通过；否则返回一句可展示的原因。"""
    props = schema.get("properties") or {}
    for key in schema.get("required") or []:
        if key not in args:
            return f"缺少必填参数 {key}"
    for key, val in args.items():
        spec = props.get(key)
        if not spec:
            available = ", ".join(props) or "无"
            return f"未知参数 {key}（可用：{available}）"
        t = spec.get("type")
        if t == "integer" and (not isinstance(val, int) or isinstance(val, bool)):
            return f"{key} 必须是整数"
        if t == "string" and not isinstance(val, str):
            return f"{key} 必须是字符串"
        if spec.get("enum") and val not in spec["enum"]:
            return f"{key} 只能是 {spec['enum']}"
        if t == "integer":
            lo, hi = spec.get("minimum"), spec.get("maximum")
            if lo is not None and val < lo:
                return f"{key} 不能小于 {lo}"
            if hi is not None and val > hi:
                return f"{key} 不能大于 {hi}"
    return ""


def _slots(obj: Any) -> list[tuple[dict, str]]:
    """收集所有「字典里的 list」位置（含嵌套），用来做截断。

    以前只找第一层：`{"small":[...], "nested":{"rows":[...]}}` 会退化成
    半截 JSON 的 preview；现在能砍到真正大的那一个。
    """
    out: list[tuple[dict, str]] = []
    if isinstance(obj, dict):
        for key, val in obj.items():
            if isinstance(val, list):
                out.append((obj, key))
            elif isinstance(val, dict):
                out.extend(_slots(val))
    return out


def _cap(payload, max_bytes: int):
    """按字节截断：反复砍「最大的那个 list」，保持结构完整。

    为什么不是直接 `str[:n]`：截一半 JSON 字符串会得到非法 JSON，模型读不了。
    """
    raw = json.dumps(payload, ensure_ascii=False)
    if len(raw.encode("utf-8")) <= max_bytes:
        return payload
    for _ in range(16):
        slots = [(c, k) for c, k in _slots(payload) if len(c[k]) > 1]
        if not slots:
            break
        container, key = max(
            slots, key=lambda s: len(json.dumps(s[0][s[1]], ensure_ascii=False)))
        lst = container[key]
        container[key] = lst[:max(1, len(lst) // 2)]
        if isinstance(payload, dict):
            payload["truncated"] = True
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) <= max_bytes:
            return payload
    # 实在塞不下（比如全是超长字符串）：给字节安全的前缀，明确标 preview。
    preview = raw.encode("utf-8")[:max(1, max_bytes // 2)].decode("utf-8", "ignore")
    return {"truncated": True, "preview": preview}


def execute(name: str, raw_args: str, ctx: ToolContext) -> dict:
    spec = registry.get(name)
    if spec is None:
        return {"error": {"code": "unknown_tool", "message": f"没有工具 {name}",
                          "available": registry.names()}}
    try:
        args = json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            raise ValueError("arguments 必须是 JSON 对象")
    except Exception as e:  # noqa: BLE001  —— 模型给的字符串什么都可能是
        return {"error": {"code": "invalid_json", "message": str(e)[:120]}}

    args = _coerce(spec.parameters, args)
    err = _validate(spec.parameters, args)
    if err:
        return {"error": {"code": "invalid_arguments", "message": err,
                          "schema": spec.parameters}}

    t0 = time.perf_counter()
    fut = _POOL.submit(spec.handler, ctx, **args)
    try:
        out = fut.result(timeout=spec.timeout_s)
    except FTimeout:
        return {"error": {"code": "timeout",
                          "message": f"{name} 超过 {spec.timeout_s}s"}}
    except ToolError as e:
        return {"error": {"code": e.code, "message": e.message}}
    except Exception as e:  # noqa: BLE001  —— 工具内部 bug 不该拖垮整轮
        return {"error": {"code": "tool_failed", "message": str(e)[:120]}}

    dt = int((time.perf_counter() - t0) * 1000)
    return {"ok": True, "data": _cap(out, spec.max_bytes), "ms": dt}

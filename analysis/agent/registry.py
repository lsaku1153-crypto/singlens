"""工具注册表。

工具模块用 `@tool` 装饰器在 import 时注册；`analysis/agent/__init__.py`
负责把工具模块 import 一次（注册副作用），否则注册表是空的。

⚠️ 这是这类注册表最经典的坑：`import analysis.agent.registry` 拿到的是
   空表，因为没人 import 过工具模块。单测里第一条就是查这个。
"""
from __future__ import annotations

import json

from analysis.agent.contracts import ToolSpec

TOOLS: dict[str, ToolSpec] = {}


def tool(name: str, description: str, parameters: dict | None = None,
         *, read_only: bool = True, max_bytes: int = 4096,
         timeout_s: float = 1.5):
    """注册一个工具。返回值就是原函数，方便直接调用与单测。"""

    def deco(fn):
        if name in TOOLS:
            raise RuntimeError(f"工具重名：{name}")
        TOOLS[name] = ToolSpec(
            name=name, description=description, parameters=parameters or {},
            handler=fn, read_only=read_only, max_bytes=max_bytes,
            timeout_s=timeout_s)
        return fn

    return deco


def get(name: str) -> ToolSpec | None:
    return TOOLS.get(name)


def schemas() -> list[dict]:
    return [s.openai_schema() for s in TOOLS.values()]


def names() -> list[str]:
    return sorted(TOOLS)


def export(path: str) -> None:
    """把全部 schema 写成 JSON，给 MCP / 文档 / 前端复用。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"tools": schemas()}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":  # python -m analysis.agent.registry
    print(json.dumps({"tools": schemas()}, ensure_ascii=False, indent=2))

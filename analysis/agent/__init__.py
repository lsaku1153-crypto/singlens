"""Agent 层。

`tools_report` / `tools_profile` 必须在包初始化时 import 一次 ——
`@tool` 装饰器靠 import 副作用注册，漏了这一步注册表是空的。
T2 会在这里追加 `from analysis.agent.loop import run`。
"""
from analysis.agent import tools_profile, tools_report  # noqa: F401

__all__ = ["tools_report", "tools_profile"]

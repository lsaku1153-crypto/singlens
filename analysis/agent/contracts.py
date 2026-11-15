"""Agent 层的数据契约。

这一层**不 import 任何 web 模块**，保证工具与循环可以脱离服务单测。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


class ToolError(Exception):
    """可回给模型的结构化错误。

    为什么要单独一个异常：模型看到 `code` 能自己改参数（比如把 `line_no`
    从字符串改成整数），看到 Python 的 `TypeError` 栈只会重复同一个调用。
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ToolContext:
    """一次运行里工具能看到的全部东西。

    `services` 是注入点：画像、推荐依赖数据库和模板，放到这里，
    工具模块本身就不用 import web，单测也能塞假实现。
    """

    report: dict
    singer_id: str = ""
    uid: str = ""
    services: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict
    handler: Callable[..., Any]
    read_only: bool = True
    max_bytes: int = 4096
    timeout_s: float = 1.5

    def openai_schema(self) -> dict:
        """转成 `/chat/completions` 的 tools 数组元素。"""
        return {"type": "function", "function": {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters or {"type": "object", "properties": {}},
        }}


@dataclass
class Budget:
    """一次运行的硬上限。写进 dataclass 而不是散在代码里，便于评测扫参数。"""

    max_steps: int = 6
    max_tool_calls: int = 10
    deadline_s: float = 25.0
    max_tokens_per_turn: int = 1200


@dataclass
class RunResult:
    answer: str
    mode: str = "agent"                 # agent | agent_repair | rule_fallback
    stop_reason: str = "answered"       # answered | empty_answer | budget_steps
                                        # | budget_tools | deadline | duplicate_calls
                                        # | verify_failed | llm_error
    steps: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    run_id: str = ""
    latency_ms: int = 0
    # 校验没过时为什么没过、被拒的草稿是什么 —— 没有这两个字段，
    # 89 条 verify_failed 的 trace 只能看到"失败了"，看不到原因。
    verify_reason: str = ""
    rejected_answer: str = ""
    # 首字延迟：从本轮开始到第一个 content 增量。流式下这是用户真正感知的延迟。
    first_delta_ms: int | None = None


# ── JSON Schema 小工具（比手写 dict 少一半括号，也少一半错）──────────
def obj(properties: dict, required: list[str] | None = None) -> dict:
    """对象 schema。

    `required=None` 表示全部必填；显式传 `[]` 表示全可选（参数有默认值时用）。
    """
    return {"type": "object", "properties": properties,
            "required": list(required if required is not None else properties.keys()),
            "additionalProperties": False}


def integer(desc: str, minimum: int | None = None,
            maximum: int | None = None) -> dict:
    out: dict[str, Any] = {"type": "integer", "description": desc}
    if minimum is not None:
        out["minimum"] = minimum
    if maximum is not None:
        out["maximum"] = maximum
    return out


def text(desc: str, enum: list[str] | None = None) -> dict:
    out: dict[str, Any] = {"type": "string", "description": desc}
    if enum:
        out["enum"] = enum
    return out

from __future__ import annotations

from dataclasses import dataclass, field

from .types import Message, ToolCall, ToolResult
from .usage import Usage


class Event:
    """所有事件的基类。"""


@dataclass
class RunStarted(Event):
    run_id: str


@dataclass
class StepStarted(Event):
    step: int


@dataclass
class TextDelta(Event):
    text: str


@dataclass
class ToolCallRequested(Event):
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class ToolStarted(Event):
    tool_call: ToolCall


@dataclass
class ToolFinished(Event):
    result: ToolResult


@dataclass
class StepFinished(Event):
    step: int


@dataclass
class RunFinished(Event):
    message: Message


@dataclass
class RunError(Event):
    error: str


@dataclass
class Progress(Event):
    """执行过程中的进度旁路事件：沙箱初始化、子 agent 派发进度等。"""
    scope: str   # "sandbox" | f"subagent:{agent}"
    text: str
    status: str | None = None   # "running" | "ok" | "error"，子 agent 每步的状态
    key: str | None = None      # 步骤合并键（工具调用 id）；前端据此把同一步的开始/完成折叠为一行


@dataclass
class ModelUsage(Event):
    usage: Usage
    cost_usd: float | None
    attempts: int
    latency_ms: float

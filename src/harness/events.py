from __future__ import annotations

from dataclasses import dataclass, field

from .types import Message, ToolCall, ToolResult


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

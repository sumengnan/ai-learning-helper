from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False
    # 工具可要求在本条 tool 结果之后追加消息（如把图片作为 user 视觉块喂给模型）。
    follow_up: list["Message"] = field(default_factory=list)


@dataclass
class ToolOutput:
    """工具可返回它替代 str：text 为回给模型的 tool 结果，follow_up 为其后追加的消息。"""
    text: str
    follow_up: list["Message"] = field(default_factory=list)


@dataclass
class Message:
    role: Role
    # content 既可为纯文本，也可为 OpenAI 多模态 content-parts 列表（[{type:text}, {type:image_url}]）
    content: "str | list | None" = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None

    def to_openai(self) -> dict:
        if self.role == Role.TOOL:
            return {
                "role": "tool",
                "tool_call_id": self.tool_call_id,
                "content": self.content or "",
            }
        msg: dict = {"role": self.role.value}
        if self.content is not None:
            msg["content"] = self.content  # list 时即多模态 content-parts，原样透传
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in self.tool_calls
            ]
        return msg

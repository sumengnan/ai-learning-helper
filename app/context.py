# app/context.py
from __future__ import annotations

from harness.state import RunState
from harness.types import Message, Role


class ConversationContextManager:
    """注入对话历史的 ContextManager（鸭子类型 harness.ContextManager）。"""

    def __init__(self, system_prompt: str, history: list[Message]) -> None:
        self._system = system_prompt
        self._history = history

    def build(self, state: RunState) -> list[Message]:
        return [Message(role=Role.SYSTEM, content=self._system), *self._history, *state.messages]

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


class LayeredContextManager:
    """分层上下文（鸭子类型 ContextManager）。各层的重活（窗口/摘要/检索）在请求前
    由 ContextAssembler 异步预算好，这里只做纯同步拼装——因为 AgentLoop 每步都同步调
    build()，绝不能在其中触发 LLM/embedding。

    布局：[system] + [摘要块?] + [相关历史片段?] + [窗口内最近原文] + [本轮新消息]
    """

    def __init__(self, system_prompt: str, summary_block: Message | None,
                 retrieved_block: Message | None, kept_history: list[Message]) -> None:
        self._system = system_prompt
        self._summary = summary_block
        self._retrieved = retrieved_block
        self._kept = kept_history

    def build(self, state: RunState) -> list[Message]:
        out = [Message(role=Role.SYSTEM, content=self._system)]
        if self._summary is not None:
            out.append(self._summary)
        if self._retrieved is not None:
            out.append(self._retrieved)
        out.extend(self._kept)
        out.extend(state.messages)
        return out

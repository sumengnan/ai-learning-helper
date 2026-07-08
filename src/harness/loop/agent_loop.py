from __future__ import annotations

import json
import uuid
from typing import AsyncIterator, Callable

from ..context.manager import ContextManager
from ..events import (
    Event, RunStarted, StepStarted, TextDelta, ToolCallRequested,
    ToolStarted, ToolFinished, StepFinished, RunFinished, RunError,
)
from ..llm.base import ModelClient, ToolCallDelta
from ..state import RunState
from ..tools.base import ToolExecutor, ToolRegistry
from ..types import Message, Role, ToolCall


def _accumulate(acc: dict[int, dict], delta: ToolCallDelta) -> None:
    slot = acc.setdefault(delta.index, {"id": None, "name": None, "args": ""})
    if delta.id:
        slot["id"] = delta.id
    if delta.name:
        slot["name"] = delta.name
    if delta.arguments:
        slot["args"] += delta.arguments


def _finalize(acc: dict[int, dict]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for idx in sorted(acc):
        slot = acc[idx]
        try:
            args = json.loads(slot["args"]) if slot["args"] else {}
        except json.JSONDecodeError:
            args = {}
        calls.append(ToolCall(
            id=slot["id"] or f"call_{idx}",
            name=slot["name"] or "",
            arguments=args,
        ))
    return calls


class AgentLoop:
    def __init__(
        self,
        client: ModelClient,
        registry: ToolRegistry,
        context: ContextManager,
        max_steps: int = 10,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._client = client
        self._registry = registry
        self._executor = ToolExecutor(registry)
        self._context = context
        self._max_steps = max_steps
        self._new_run_id = run_id_factory or (lambda: uuid.uuid4().hex)

    async def run(self, user_message: str) -> AsyncIterator[Event]:
        state = RunState(run_id=self._new_run_id())
        state.append(Message(role=Role.USER, content=user_message))
        yield RunStarted(run_id=state.run_id)

        for step in range(1, self._max_steps + 1):
            state.step = step
            yield StepStarted(step=step)

            messages = self._context.build(state)
            content_parts: list[str] = []
            tool_acc: dict[int, dict] = {}
            try:
                async for chunk in self._client.stream(messages, self._registry.schemas()):
                    if chunk.type == "text" and chunk.text:
                        content_parts.append(chunk.text)
                        yield TextDelta(text=chunk.text)
                    elif chunk.type == "tool_call" and chunk.tool_call_delta:
                        _accumulate(tool_acc, chunk.tool_call_delta)
            except Exception as e:
                yield RunError(error=f"模型调用失败: {e}")
                return

            tool_calls = _finalize(tool_acc)
            assistant = Message(
                role=Role.ASSISTANT,
                content="".join(content_parts) or None,
                tool_calls=tool_calls,
            )
            state.append(assistant)

            if not tool_calls:  # 终止条件①：模型不再要工具
                yield RunFinished(message=assistant)
                return

            yield ToolCallRequested(tool_calls=tool_calls)
            for tc in tool_calls:  # v1 顺序执行
                yield ToolStarted(tool_call=tc)
                result = await self._executor.execute(tc)
                state.append(Message(
                    role=Role.TOOL, content=result.content, tool_call_id=tc.id))
                yield ToolFinished(result=result)
            yield StepFinished(step=step)

        yield RunError(error=f"达到 max_steps 上限 ({self._max_steps})")

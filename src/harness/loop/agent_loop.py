# src/harness/loop/agent_loop.py
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import AsyncIterator, Callable

from opentelemetry.trace import Status, StatusCode

from ..context.manager import ContextManager
from ..events import (
    Event, RunStarted, StepStarted, TextDelta, ToolCallRequested,
    ToolStarted, ToolFinished, StepFinished, RunFinished, RunError, ModelUsage,
)
from ..llm.base import ModelClient, ToolCallDelta
from ..reliability.budget import BudgetTracker, BudgetExceeded
from ..state import RunState
from ..telemetry.tracer import get_tracer
from ..tools.base import ToolExecutor, ToolRegistry
from ..types import Message, Role, ToolCall, ToolResult
from ..usage import cost_usd


@dataclass
class _Finalized:
    call: ToolCall
    parse_error: str | None


def _accumulate(acc: dict[int, dict], delta: ToolCallDelta) -> None:
    slot = acc.setdefault(delta.index, {"id": None, "name": None, "args": ""})
    if delta.id:
        slot["id"] = delta.id
    if delta.name:
        slot["name"] = delta.name
    if delta.arguments:
        slot["args"] += delta.arguments


def _finalize(acc: dict[int, dict]) -> list[_Finalized]:
    out: list[_Finalized] = []
    for idx in sorted(acc):
        slot = acc[idx]
        parse_error: str | None = None
        args: dict = {}
        if slot["args"]:
            try:
                parsed = json.loads(slot["args"])
                if isinstance(parsed, dict):
                    args = parsed
                else:
                    parse_error = f"参数需为 JSON 对象，收到：{slot['args'][:80]}"
            except json.JSONDecodeError as e:
                parse_error = f"{e}：{slot['args'][:80]}"
        out.append(_Finalized(
            call=ToolCall(id=slot["id"] or f"call_{idx}", name=slot["name"] or "", arguments=args),
            parse_error=parse_error,
        ))
    return out


class AgentLoop:
    def __init__(
        self,
        client: ModelClient,
        registry: ToolRegistry,
        context: ContextManager,
        max_steps: int = 10,
        run_id_factory: Callable[[], str] | None = None,
        budget: BudgetTracker | None = None,
        tracer=None,
        model_name: str = "",
        price_map: dict | None = None,
        tool_result_max_chars: int | None = None,
        checkpoint_store=None,
    ) -> None:
        self._client = client
        self._registry = registry
        self._executor = ToolExecutor(registry, max_chars=tool_result_max_chars)
        self._context = context
        self._max_steps = max_steps
        self._new_run_id = run_id_factory or (lambda: uuid.uuid4().hex)
        self._budget = budget
        self._tracer = tracer or get_tracer()
        self._model_name = model_name
        self._price_map = price_map or {}
        self._checkpoint_store = checkpoint_store

    async def run(self, user_message: str) -> AsyncIterator[Event]:
        state = RunState(run_id=self._new_run_id())
        state.append(Message(role=Role.USER, content=user_message))
        async for ev in self._run_from(state, resuming=False):
            yield ev

    async def resume(self, run_id: str) -> AsyncIterator[Event]:
        """从 checkpoint 续跑 run_id。

        已知限制：从上一个完整步的下一步重跑；若中断发生在某步执行到一半，该步
        整步重跑，其中的**有副作用工具**（write_file / run_shell 等）可能被**重复
        执行**。调用方需保证工具幂等或自行去重。
        """
        if self._checkpoint_store is None:
            yield RunError(error="未配置 checkpoint_store，无法 resume")
            return
        state = self._checkpoint_store.load(run_id)
        if state is None:
            yield RunError(error=f"无 checkpoint：{run_id}")
            return
        async for ev in self._run_from(state, resuming=True):
            yield ev

    async def _run_from(self, state: RunState, resuming: bool) -> AsyncIterator[Event]:
        if self._budget:
            self._budget.start()
        # 回填 run_id 到审批上下文（若有），使 ApprovalRequired 事件自洽携带 run_id
        from ..approval import set_run_id
        set_run_id(state.run_id)
        if not resuming:
            yield RunStarted(run_id=state.run_id)

        with self._tracer.start_as_current_span("run") as run_span:
            run_span.set_attribute("harness.run_id", state.run_id)

            for step in range(state.step + 1, self._max_steps + 1):
                state.step = step

                if self._budget:  # 步边界预算检查
                    try:
                        self._budget.check()
                    except BudgetExceeded as e:
                        run_span.set_status(Status(StatusCode.ERROR, e.reason))
                        yield RunError(error=e.reason)
                        return

                yield StepStarted(step=step)

                with self._tracer.start_as_current_span("step") as step_span:
                    step_span.set_attribute("harness.step", step)

                    messages = self._context.build(state)
                    content_parts: list[str] = []
                    tool_acc: dict[int, dict] = {}
                    usage = None
                    attempts = 1
                    t0 = time.monotonic()
                    try:
                        with self._tracer.start_as_current_span("model_call") as mc_span:
                            mc_span.set_attribute("harness.model", self._model_name)
                            async for chunk in self._client.stream(messages, self._registry.schemas()):
                                if chunk.type == "text" and chunk.text:
                                    content_parts.append(chunk.text)
                                    yield TextDelta(text=chunk.text)
                                elif chunk.type == "tool_call" and chunk.tool_call_delta:
                                    _accumulate(tool_acc, chunk.tool_call_delta)
                                elif chunk.type == "done":
                                    usage = chunk.usage
                                    attempts = chunk.attempts
                            if usage is not None:
                                mc_span.set_attribute("harness.tokens.total", usage.total_tokens)
                            mc_span.set_attribute("harness.attempts", attempts)
                    except Exception as e:
                        step_span.set_status(Status(StatusCode.ERROR, str(e)))
                        yield RunError(error=f"模型调用失败: {e}")
                        return

                    latency_ms = (time.monotonic() - t0) * 1000
                    if usage is not None:
                        cost = cost_usd(usage, self._model_name, self._price_map)
                        if self._budget:
                            self._budget.add_usage(usage)
                        yield ModelUsage(usage=usage, cost_usd=cost, attempts=attempts, latency_ms=latency_ms)

                    finalized = _finalize(tool_acc)
                    tool_calls = [f.call for f in finalized]
                    assistant = Message(
                        role=Role.ASSISTANT,
                        content="".join(content_parts) or None,
                        tool_calls=tool_calls,
                    )
                    state.append(assistant)

                    if not tool_calls:
                        yield RunFinished(message=assistant)
                        if self._checkpoint_store:
                            self._checkpoint_store.delete(state.run_id)
                        return

                    yield ToolCallRequested(tool_calls=tool_calls)
                    for f in finalized:
                        tc = f.call
                        yield ToolStarted(tool_call=tc)
                        with self._tracer.start_as_current_span(f"tool_call:{tc.name}") as ts:
                            if f.parse_error:  # 自纠正：回填明确错误，让模型下一步重发
                                result = ToolResult(
                                    tc.id,
                                    f"工具调用参数不是合法 JSON：{f.parse_error}，请重新调用。",
                                    is_error=True,
                                )
                            else:
                                result = await self._executor.execute(tc)
                            ts.set_attribute("harness.tool.is_error", result.is_error)
                            if result.is_error:
                                ts.set_status(Status(StatusCode.ERROR, result.content[:200]))
                                ts.add_event("tool.error", {"content": result.content[:200]})
                        state.append(Message(role=Role.TOOL, content=result.content, tool_call_id=tc.id))
                        # 工具追加的后续消息（如把图片作为 user 视觉块注入）：接在 tool 结果之后
                        for fm in result.follow_up:
                            state.append(fm)
                        yield ToolFinished(result=result)
                    yield StepFinished(step=step)
                    if self._checkpoint_store:  # 步边界存快照
                        self._checkpoint_store.save(state)

            yield RunError(error=f"达到 max_steps 上限 ({self._max_steps})")

import pytest

from harness.loop.agent_loop import AgentLoop
from harness.context.manager import ContextManager
from harness.tools.base import ToolRegistry
from harness.tools.builtins.calculator import CalculatorTool
from harness.events import (
    RunStarted, TextDelta, ToolCallRequested, ToolStarted, ToolFinished,
    RunFinished, RunError, StepStarted,
)


def _build_loop(client, max_steps=10):
    reg = ToolRegistry()
    reg.register(CalculatorTool())
    ctx = ContextManager(system_prompt="s")
    return AgentLoop(
        client=client, registry=reg, context=ctx,
        max_steps=max_steps, run_id_factory=lambda: "run-test",
    )


async def _collect(loop, msg):
    return [ev async for ev in loop.run(msg)]


async def test_plain_chat_terminates_without_tools(make_mock, text_turn):
    loop = _build_loop(make_mock([text_turn("你好呀")]))
    events = await _collect(loop, "hi")
    assert isinstance(events[0], RunStarted)
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "你好呀"
    assert isinstance(events[-1], RunFinished)
    assert events[-1].message.content == "你好呀"
    # 未触发任何工具
    assert not any(isinstance(e, ToolStarted) for e in events)


async def test_tool_call_executes_and_feeds_back(make_mock, text_turn, tool_turn):
    client = make_mock([
        tool_turn("calculator", '{"expression": "(12+8)*3"}', call_id="c1"),
        text_turn("答案是 60"),
    ])
    loop = _build_loop(client)
    events = await _collect(loop, "算 (12+8)*3")
    assert any(isinstance(e, ToolCallRequested) for e in events)
    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert len(finished) == 1
    assert finished[0].result.content == "60"
    assert finished[0].result.is_error is False
    assert isinstance(events[-1], RunFinished)
    assert events[-1].message.content == "答案是 60"


async def test_max_steps_guard_emits_run_error(make_mock, tool_turn):
    # 每轮都请求工具，永不给最终答案 → 应在 max_steps 后 RunError
    turns = [tool_turn("calculator", '{"expression": "1+1"}', call_id=f"c{i}")
             for i in range(5)]
    loop = _build_loop(make_mock(turns), max_steps=2)
    events = await _collect(loop, "loop forever")
    assert isinstance(events[-1], RunError)
    assert "max_steps" in events[-1].error
    # 恰好 2 个 StepStarted
    assert sum(isinstance(e, StepStarted) for e in events) == 2


async def test_llm_stream_exception_becomes_run_error(text_turn):
    class BoomClient:
        async def stream(self, messages, tools):
            raise ConnectionError("network down")
            yield  # pragma: no cover  (使其成为 async generator)

    loop = _build_loop(BoomClient())
    events = await _collect(loop, "hi")
    assert isinstance(events[-1], RunError)
    assert "network down" in events[-1].error


async def test_bad_tool_args_feed_back_is_error(make_mock, text_turn, tool_turn):
    # 参数缺 expression → executor 返回 is_error，loop 照常回填并继续
    client = make_mock([
        tool_turn("calculator", '{}', call_id="c1"),
        text_turn("我需要一个表达式"),
    ])
    loop = _build_loop(client)
    events = await _collect(loop, "算点啥")
    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert finished[0].result.is_error is True
    assert isinstance(events[-1], RunFinished)

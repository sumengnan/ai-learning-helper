import pytest

from harness.loop.agent_loop import AgentLoop, _accumulate, _finalize
from harness.llm.base import ToolCallDelta
from harness.context.manager import ContextManager
from harness.tools.base import ToolRegistry
from harness.tools.builtins.calculator import CalculatorTool
from harness.events import (
    RunStarted, TextDelta, ToolCallRequested, ToolStarted, ToolFinished,
    RunFinished, RunError, StepStarted,
)
from harness.reliability.budget import BudgetTracker
from harness.events import ModelUsage


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


def _build_loop_with_budget(client, budget, max_steps=10):
    reg = ToolRegistry()
    reg.register(CalculatorTool())
    ctx = ContextManager(system_prompt="s")
    return AgentLoop(client=client, registry=reg, context=ctx, max_steps=max_steps,
                     run_id_factory=lambda: "run-test", budget=budget)


def test_finalize_returns_calls_and_parse_error():
    # 交错双工具 + 一个非法 JSON，验证新的 _Finalized 返回
    acc = {}
    _accumulate(acc, ToolCallDelta(index=0, id="a", name="calculator", arguments='{"expression":'))
    _accumulate(acc, ToolCallDelta(index=1, id="b", name="echo", arguments='{"text":"hi"}'))
    _accumulate(acc, ToolCallDelta(index=0, arguments='"1+1"}'))
    out = _finalize(acc)
    assert [f.call.name for f in out] == ["calculator", "echo"]
    assert out[0].call.arguments == {"expression": "1+1"}
    assert out[0].parse_error is None


def test_finalize_flags_invalid_json():
    acc = {}
    _accumulate(acc, ToolCallDelta(index=0, id="a", name="echo", arguments="not-json"))
    out = _finalize(acc)
    assert out[0].parse_error is not None
    assert out[0].call.arguments == {}


async def test_invalid_json_tool_args_self_correct(make_mock, text_turn):
    # 第一轮吐非法 JSON 工具参数 → loop 应回填 is_error 错误消息，不崩溃；第二轮作答
    from harness.llm.base import StreamChunk
    bad_tool_turn = [
        StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
            index=0, id="c1", name="calculator", arguments="not-json")),
        StreamChunk(type="done"),
    ]
    loop = _build_loop(make_mock([bad_tool_turn, text_turn("抱歉，我重发")]))
    from harness.events import ToolFinished, RunFinished
    events = [e async for e in loop.run("算点啥")]
    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert finished[0].result.is_error is True
    assert "JSON" in finished[0].result.content
    assert isinstance(events[-1], RunFinished)


async def test_token_budget_breach_emits_run_error(make_mock):
    # 预算 50，每轮请求工具且 usage=40：step1 后累计 40，step2 后 80，step3 步边界拦截
    from harness.events import RunError
    from harness.usage import Usage
    from harness.llm.base import StreamChunk

    def tool_usage_turn(i):
        return [
            StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
                index=0, id=f"c{i}", name="calculator", arguments='{"expression":"1+1"}')),
            StreamChunk(type="done", usage=Usage(20, 20, 40)),
        ]

    loop = _build_loop_with_budget(make_mock([tool_usage_turn(i) for i in range(5)]),
                                   BudgetTracker(max_tokens=50))
    events = [e async for e in loop.run("go")]
    assert isinstance(events[-1], RunError)
    assert "token" in events[-1].error


async def test_model_usage_event_emitted(make_mock, text_turn_usage):
    from harness.events import ModelUsage
    loop = _build_loop(make_mock([text_turn_usage("你好", prompt=10, completion=5)]))
    events = [e async for e in loop.run("hi")]
    mu = [e for e in events if isinstance(e, ModelUsage)]
    assert len(mu) == 1
    assert mu[0].usage.total_tokens == 15

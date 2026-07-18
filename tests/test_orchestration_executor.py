from harness.events import Progress
from harness.llm.base import StreamChunk
from harness.tools.base import ToolRegistry
from app.orchestration.executor import Executor, StepArtifact
from app.orchestration.plan import Artifact, PlanStep


def _step(deps=()):
    return PlanStep(id="s1", description="回答质数定义", expected="质数定义",
                    depends_on=list(deps))


def _text_only_turns(text):
    # 一轮：吐正文 + done（无工具）→ AgentLoop 直接 RunFinished
    return [[StreamChunk(type="text", text=text), StreamChunk(type="done")]]


async def _collect(gen):
    events, artifact = [], None
    async for ev in gen:
        if isinstance(ev, StepArtifact):
            artifact = ev.artifact
            # 便于测试直接查 artifact.error，而不必额外返回整个 StepArtifact 信号
            artifact.error = ev.error
        else:
            events.append(ev)
    return events, artifact


async def test_executor_produces_artifact_from_final_text(make_mock):
    client = make_mock(_text_only_turns("质数是只有1和自身两个因子的自然数"))
    ex = Executor(client=client, registry=ToolRegistry(), system_prompt="你是执行者", model="m", max_steps=3)
    events, artifact = await _collect(ex.execute(_step(), {}))
    assert isinstance(artifact, Artifact)
    assert "质数" in artifact.summary


async def test_executor_emits_progress_not_textdelta(make_mock):
    """执行者内部产出不得作为 TextDelta 泄露给用户；只出 Progress。"""
    client = make_mock(_text_only_turns("中间产出"))
    ex = Executor(client=client, registry=ToolRegistry(), system_prompt="sp", model="m", max_steps=3)
    events, _ = await _collect(ex.execute(_step(), {}))
    from harness.events import TextDelta
    assert not any(isinstance(e, TextDelta) for e in events)
    assert all(isinstance(e, Progress) for e in events)


def test_build_prompt_includes_deps_and_hint():
    from app.orchestration.executor import _build_prompt
    p = _build_prompt(_step(deps=["s0"]), {"s0": Artifact(summary="前置：X=42")}, hint="补充示例")
    assert "回答质数定义" in p and "质数定义" in p
    assert "[s0]" in p and "前置：X=42" in p
    assert "补充示例" in p


def _tool_then_done_turns():
    from harness.llm.base import ToolCallDelta
    return [
        [StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
            index=0, id="c1", name="calculator", arguments='{"expression": "1+1"}')),
         StreamChunk(type="done")],
        [StreamChunk(type="text", text="算完了"), StreamChunk(type="done")],
    ]


async def test_executor_tool_call_emits_progress(make_mock):
    from harness.tools.builtins.calculator import CalculatorTool
    reg = ToolRegistry(); reg.register(CalculatorTool())
    client = make_mock(_tool_then_done_turns())
    ex = Executor(client=client, registry=reg, system_prompt="sp", model="m", max_steps=3)
    events, artifact = await _collect(ex.execute(_step(), {}))
    progs = [e for e in events if isinstance(e, Progress)]
    assert any(p.scope == "subagent:executor:s1" for p in progs)
    assert any(p.status == "running" for p in progs)
    assert any(p.status == "ok" for p in progs)
    assert "算完了" in artifact.summary


def _always_tool_turns():
    from harness.llm.base import ToolCallDelta
    return [[StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
        index=0, id="c1", name="calculator", arguments='{"expression":"1+1"}')),
        StreamChunk(type="done")]]


async def test_executor_emits_step_header_and_tool_detail(make_mock):
    """首个进度是步骤描述头行；工具行带 detail（tool+args），完成行 detail 带 result+is_error 且保留 args。"""
    from harness.tools.builtins.calculator import CalculatorTool
    reg = ToolRegistry(); reg.register(CalculatorTool())
    client = make_mock(_tool_then_done_turns())
    ex = Executor(client=client, registry=reg, system_prompt="sp", model="m", max_steps=3)
    events, _ = await _collect(ex.execute(_step(), {}))
    progs = [e for e in events if isinstance(e, Progress)]
    # (a) 头行：文字=步骤描述，key=__hdr__:s1，无 detail
    assert progs[0].text == "回答质数定义" and progs[0].key == "__hdr__:s1"
    assert progs[0].detail is None
    # (b) 工具开始行 detail 带 tool + args
    started = [p for p in progs if p.status == "running" and p.detail]
    assert any(p.detail["tool"] == "calculator" and p.detail["args"] == {"expression": "1+1"}
               for p in started)
    # (c) 工具完成行 detail 带 result + is_error，且仍保留 args（前端按 key 合并只留最后一条）
    finished = [p for p in progs if p.status in ("ok", "error") and p.detail and "result" in p.detail]
    assert finished and finished[-1].detail["args"] == {"expression": "1+1"}
    assert "is_error" in finished[-1].detail


async def test_executor_runerror_sets_error_and_empty_summary(make_mock):
    from harness.tools.builtins.calculator import CalculatorTool
    reg = ToolRegistry(); reg.register(CalculatorTool())
    client = make_mock(_always_tool_turns())
    ex = Executor(client=client, registry=reg, system_prompt="sp", model="m", max_steps=1)
    events, artifact = await _collect(ex.execute(_step(), {}))
    assert artifact.error is not None
    assert artifact.summary == ""

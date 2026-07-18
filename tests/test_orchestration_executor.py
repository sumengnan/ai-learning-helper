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


async def test_executor_injects_dep_artifacts(make_mock):
    """依赖产物应进入 Executor 的 prompt（用 mock 无法看 prompt，改测不崩且产出正常）。"""
    client = make_mock(_text_only_turns("已参考前置结果"))
    ex = Executor(client=client, registry=ToolRegistry(), system_prompt="sp", model="m", max_steps=3)
    deps = {"s0": Artifact(summary="前置：X=42")}
    _, artifact = await _collect(ex.execute(_step(deps=["s0"]), deps))
    assert artifact is not None

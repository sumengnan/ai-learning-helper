from harness.loop.agent_loop import AgentLoop
from harness.context.manager import ContextManager
from harness.tools.base import ToolRegistry
from harness.tools.builtins.calculator import CalculatorTool
from harness.persistence.checkpoint import CheckpointStore
from harness.state import RunState
from harness.types import Message, Role, ToolCall
from harness.llm.base import StreamChunk, ToolCallDelta
from harness.events import RunFinished, RunError, StepStarted, RunStarted


class _RecordingStore:
    def __init__(self): self.saves = []; self.deleted = []
    def save(self, state): self.saves.append(state.step)
    def load(self, run_id): return None
    def delete(self, run_id): self.deleted.append(run_id)


def _reg():
    r = ToolRegistry(); r.register(CalculatorTool()); return r


async def test_checkpoint_saved_each_step_deleted_on_finish(make_mock, text_turn):
    store = _RecordingStore()
    tool_turn = [StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
        index=0, id="c1", name="calculator", arguments='{"expression":"1+1"}')),
        StreamChunk(type="done")]
    loop = AgentLoop(client=make_mock([tool_turn, text_turn("答案 2")]),
                     registry=_reg(), context=ContextManager("s"), max_steps=5,
                     run_id_factory=lambda: "r1", checkpoint_store=store)
    _ = [e async for e in loop.run("算 1+1")]
    assert store.saves == [1]              # step1 结束存一次
    assert store.deleted == ["r1"]         # RunFinished 删


async def test_backward_compat_no_store_same_events(make_mock, text_turn):
    loop = AgentLoop(client=make_mock([text_turn("hi")]), registry=ToolRegistry(),
                     context=ContextManager("s"), max_steps=5, run_id_factory=lambda: "r1")
    events = [e async for e in loop.run("hi")]
    assert isinstance(events[0], RunStarted) and isinstance(events[-1], RunFinished)


async def test_resume_continues_from_checkpoint(make_mock, text_turn):
    cs = CheckpointStore(":memory:")
    st = RunState(run_id="r1"); st.step = 1
    st.append(Message(role=Role.USER, content="算 1+1"))
    st.append(Message(role=Role.ASSISTANT, content=None,
                      tool_calls=[ToolCall(id="c1", name="calculator", arguments={"expression": "1+1"})]))
    st.append(Message(role=Role.TOOL, content="2", tool_call_id="c1"))
    cs.save(st)

    loop = AgentLoop(client=make_mock([text_turn("最终答案 2")]),
                     registry=_reg(), context=ContextManager("s"), max_steps=5,
                     checkpoint_store=cs)
    events = [e async for e in loop.resume("r1")]
    assert not any(isinstance(e, RunStarted) for e in events)          # resume 不再发 RunStarted
    assert [e.step for e in events if isinstance(e, StepStarted)] == [2]  # 从 step2 续
    assert isinstance(events[-1], RunFinished)
    assert "最终答案 2" in events[-1].message.content
    assert cs.load("r1") is None                                       # 完成后快照删


async def test_resume_missing_checkpoint_run_error(make_mock, text_turn):
    loop = AgentLoop(client=make_mock([text_turn("x")]), registry=ToolRegistry(),
                     context=ContextManager("s"), checkpoint_store=CheckpointStore(":memory:"))
    events = [e async for e in loop.resume("nope")]
    assert isinstance(events[-1], RunError) and "无 checkpoint" in events[-1].error

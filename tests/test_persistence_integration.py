from harness.loop.agent_loop import AgentLoop
from harness.context.manager import ContextManager
from harness.tools.base import ToolRegistry
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink


async def test_real_loop_trajectory_persisted(make_mock, text_turn):
    store = TrajectoryStore(":memory:")
    sink = TrajectorySink(store)
    loop = AgentLoop(client=make_mock([text_turn("你好")]), registry=ToolRegistry(),
                     context=ContextManager("s"), max_steps=5, run_id_factory=lambda: "r1")
    _ = [e async for e in sink.wrap(loop.run("hi"))]
    types = [e["type"] for e in store.load("r1")]
    assert types[0] == "RunStarted"
    assert types[-1] == "RunFinished"
    assert "TextDelta" in types

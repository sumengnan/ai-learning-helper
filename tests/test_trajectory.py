from harness.persistence.trajectory import TrajectoryStore, TrajectorySink
from harness.events import RunStarted, TextDelta, RunFinished
from harness.types import Message, Role


async def _gen(evs):
    for e in evs:
        yield e


def test_store_append_load_ordered():
    s = TrajectoryStore(":memory:")
    s.append("r1", 0, {"type": "A", "data": {}})
    s.append("r1", 1, {"type": "B", "data": {}})
    assert [e["type"] for e in s.load("r1")] == ["A", "B"]


async def test_sink_records_and_passes_through():
    store = TrajectoryStore(":memory:")
    sink = TrajectorySink(store)
    evs = [RunStarted(run_id="r1"), TextDelta(text="hi"),
           RunFinished(message=Message(role=Role.ASSISTANT, content="done"))]
    out = [e async for e in sink.wrap(_gen(evs))]
    assert len(out) == 3                               # 透传
    assert [e["type"] for e in store.load("r1")] == ["RunStarted", "TextDelta", "RunFinished"]

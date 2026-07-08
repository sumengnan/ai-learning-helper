from harness.memory.memory import Memory
from harness.memory.store import MemoryStore
from harness.tools.base import ToolRegistry, ToolExecutor
from harness.tools.builtins.memory_search import SearchMemoryTool
from harness.tools.builtins.memory_write import RememberTool
from harness.types import ToolCall


def _mem(mock_embedder):
    return Memory(MemoryStore(":memory:", dimension=64),
                  mock_embedder(dimension=64), chunk_size=1000, overlap=0)


async def test_remember_then_search_roundtrip(mock_embedder):
    mem = _mem(mock_embedder)
    reg = ToolRegistry()
    reg.register(RememberTool(mem))
    reg.register(SearchMemoryTool(mem))
    ex = ToolExecutor(reg)

    w = await ex.execute(ToolCall(id="c1", name="remember",
                                  arguments={"text": "the cat sat on the mat"}))
    assert w.is_error is False
    assert "已记住" in w.content

    r = await ex.execute(ToolCall(id="c2", name="search_memory",
                                  arguments={"query": "cat", "k": 3}))
    assert r.is_error is False
    assert "the cat sat on the mat" in r.content


async def test_search_no_hits_message(mock_embedder):
    mem = _mem(mock_embedder)
    reg = ToolRegistry()
    reg.register(SearchMemoryTool(mem))
    ex = ToolExecutor(reg)
    r = await ex.execute(ToolCall(id="c1", name="search_memory",
                                  arguments={"query": "anything", "k": 3}))
    assert "未在知识库" in r.content

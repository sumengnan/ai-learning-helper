from harness.memory.memory import Memory
from harness.memory.sqlite_backend import SqliteVecBackend
from harness.tools.base import ToolRegistry, ToolExecutor
from harness.tools.builtins.memory_search import SearchMemoryTool
from harness.tools.builtins.memory_write import RememberTool
from harness.types import ToolCall


def _mem(mock_embedder):
    return Memory(SqliteVecBackend(":memory:", dimension=64),
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


async def test_search_uses_default_k_when_k_omitted(mock_embedder):
    # 不传 k 时应回落到 default_k；default_k=1 → 只返回 1 条
    mem = _mem(mock_embedder)
    reg = ToolRegistry()
    reg.register(SearchMemoryTool(mem, default_k=1))
    ex = ToolExecutor(reg)
    await ex.execute(ToolCall(id="w1", name="search_memory", arguments={"query": "seed"}))  # noqa
    # 先写入两条含相同词的记忆
    from harness.tools.builtins.memory_write import RememberTool
    reg.register(RememberTool(mem))
    await ex.execute(ToolCall(id="w2", name="remember",
                              arguments={"text": "cat one"}))
    await ex.execute(ToolCall(id="w3", name="remember",
                              arguments={"text": "cat two"}))
    r = await ex.execute(ToolCall(id="c1", name="search_memory",
                                  arguments={"query": "cat"}))
    assert r.is_error is False
    # default_k=1 → 只有一行结果
    assert len([ln for ln in r.content.splitlines() if ln.strip()]) == 1

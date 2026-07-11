# tests/test_reranker.py
from harness.memory.reranker import NoOpReranker, Reranker


async def test_noop_preserves_order():
    r = NoOpReranker()
    items = ["a", "b", "c"]
    assert await r.rerank("q", items) == ["a", "b", "c"]


def test_protocol_has_rerank():
    assert hasattr(Reranker, "rerank")

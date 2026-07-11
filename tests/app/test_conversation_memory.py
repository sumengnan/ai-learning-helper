from app.conversation_memory import ConversationMemoryService
from harness.memory.memory import Memory
from harness.memory.store import MemoryStore


def _service(mock_embedder):
    store = MemoryStore(":memory:", dimension=64)
    mem = Memory(store, mock_embedder(dimension=64), chunk_size=1000, overlap=0)
    return ConversationMemoryService(mem)


async def test_record_then_retrieve(mock_embedder):
    svc = _service(mock_embedder)
    await svc.record_turn("c1", seq=0, text="python programming language")
    await svc.record_turn("c1", seq=2, text="the cat sat on the mat")
    hits = await svc.retrieve("c1", "cat", k=1)
    assert hits and hits[0].text == "the cat sat on the mat"


async def test_conversations_are_isolated(mock_embedder):
    svc = _service(mock_embedder)
    await svc.record_turn("c1", seq=0, text="the cat sat on the mat")
    await svc.record_turn("c2", seq=0, text="the cat sat on the mat")
    hits = await svc.retrieve("c2", "cat", k=5)
    assert len(hits) == 1                      # 只召回 c2 自己的


async def test_before_seq_excludes_in_window_turns(mock_embedder):
    """窗口内的轮（seq >= 窗口起点）不应被重复召回。"""
    svc = _service(mock_embedder)
    await svc.record_turn("c1", seq=0, text="the cat sat on the mat")   # 窗口外
    await svc.record_turn("c1", seq=4, text="a cat and a hat")          # 窗口内
    hits = await svc.retrieve("c1", "cat", k=5, before_seq=4)
    texts = [h.text for h in hits]
    assert "the cat sat on the mat" in texts
    assert "a cat and a hat" not in texts


async def test_empty_query_returns_empty(mock_embedder):
    svc = _service(mock_embedder)
    await svc.record_turn("c1", seq=0, text="something")
    assert await svc.retrieve("c1", "   ", k=5) == []


async def test_empty_store_returns_empty(mock_embedder):
    svc = _service(mock_embedder)
    assert await svc.retrieve("c1", "anything", k=5) == []


async def test_record_empty_text_is_noop(mock_embedder):
    svc = _service(mock_embedder)
    assert await svc.record_turn("c1", seq=0, text="   ") == []

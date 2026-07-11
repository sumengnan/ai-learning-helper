from app.conversation_memory import ConversationMemoryService
from harness.memory.memory import Memory
from harness.memory.sqlite_backend import SqliteVecBackend


def _service(mock_embedder):
    store = SqliteVecBackend(":memory:", dimension=64)
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


class _FakeWriter:
    def __init__(self):
        self.calls = []
    async def write(self, owner_id, kind, text):
        self.calls.append((owner_id, kind, text))
        return ["new1"]


async def test_record_turn_uses_writer_when_present(mock_embedder):
    from app.conversation_memory import ConversationMemoryService
    from harness.memory.memory import Memory
    from harness.memory.sqlite_backend import SqliteVecBackend
    mem = Memory(SqliteVecBackend(":memory:", dimension=64), mock_embedder(dimension=64))
    fw = _FakeWriter()
    svc = ConversationMemoryService(mem, writer=fw)
    out = await svc.record_turn("c1", 0, "我在学 Python")
    assert out == ["new1"]
    assert fw.calls == [("c1", "conversation", "我在学 Python")]


async def test_record_turn_default_raw_when_no_writer(mock_embedder):
    from app.conversation_memory import ConversationMemoryService
    from harness.memory.memory import Memory
    from harness.memory.sqlite_backend import SqliteVecBackend
    mem = Memory(SqliteVecBackend(":memory:", dimension=64), mock_embedder(dimension=64))
    svc = ConversationMemoryService(mem)
    await svc.record_turn("c1", 0, "快速排序是一种排序算法")
    hits = await svc.retrieve("c1", "排序算法", k=1)
    assert hits and hits[0].text == "快速排序是一种排序算法"


async def test_retrieve_facts_without_seq_always_eligible(mock_embedder):
    """提炼事实（无 seq 元数据）不受 before_seq 窗口过滤，恒可召回；原文轮次仍按 seq 过滤。"""
    from app.conversation_memory import ConversationMemoryService
    from harness.memory.memory import Memory
    from harness.memory.sqlite_backend import SqliteVecBackend
    mem = Memory(SqliteVecBackend(":memory:", dimension=64), mock_embedder(dimension=64))
    svc = ConversationMemoryService(mem)
    # 原文轮次：seq=5（窗口内，应被 before_seq=3 过滤掉）
    await mem.add_texts(["排序算法的原文轮次"], "conversation:c1", {"seq": 5})
    # 提炼事实：无 seq（应恒可召回）
    await mem.add_texts(["用户在学排序算法"], "conversation:c1", {})
    hits = await svc.retrieve("c1", "排序算法", k=5, before_seq=3)
    texts = [h.text for h in hits]
    assert "用户在学排序算法" in texts          # 无 seq 的事实恒可召回
    assert "排序算法的原文轮次" not in texts     # seq=5 >= 3 的原文轮次被过滤

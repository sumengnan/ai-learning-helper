# tests/app/test_knowledge.py
from app.knowledge import KnowledgeService, EmptyDocument
from app.documents import DocumentStore
from harness.memory.memory import Memory
from harness.memory.sqlite_backend import SqliteVecBackend
import pytest


def _service(mock_embedder):
    store = SqliteVecBackend(":memory:", dimension=64)
    mem = Memory(store, mock_embedder(dimension=64), chunk_size=1000, overlap=0)
    return KnowledgeService(mem, store, DocumentStore(":memory:")), mem


async def test_ingest_registers_and_searchable(mock_embedder):
    svc, mem = _service(mock_embedder)
    doc = await svc.ingest("u1", "bio.txt", "光合作用把二氧化碳和水转化为氧气".encode())
    assert doc["num_chunks"] >= 1 and doc["filename"] == "bio.txt"
    hits = await mem.search("光合作用", "knowledge:u1", 3)
    assert any("光合作用" in h.text for h in hits)


async def test_delete_removes_chunks_and_doc(mock_embedder):
    svc, mem = _service(mock_embedder)
    doc = await svc.ingest("u1", "a.txt", "独特内容ABC".encode())
    svc.delete("u1", doc["id"])
    assert await mem.search("独特内容", "knowledge:u1", 3) == []
    assert svc._doc_store.exists("u1", doc["id"]) is False


async def test_empty_document_raises(mock_embedder):
    svc, _ = _service(mock_embedder)
    with pytest.raises(EmptyDocument):
        await svc.ingest("u1", "empty.txt", "   ".encode())


async def test_ingest_text_shows_in_user_knowledge_list(mock_embedder):
    """聊天中保存的文本应作为正式文档进入该用户的知识库列表（owner=user_id）。"""
    svc, mem = _service(mock_embedder)
    doc = await svc.ingest_text("u1", "AI 最新数据", "2026 年模型参数规模持续增长")
    assert doc["num_chunks"] >= 1 and doc["filename"] == "AI 最新数据"
    # 出现在知识库菜单（list_fragments 按 owner=user_id 过滤）
    page = svc.list_fragments("u1", 1, 10)
    assert page["total"] >= 1
    assert any(item["filename"] == "AI 最新数据" for item in page["items"])
    # 建立了 doc_store 文档记录（可删除）
    assert svc._doc_store.exists("u1", doc["id"]) is True
    # 语义可检索
    hits = await mem.search("模型参数", "knowledge:u1", 3)
    assert any("参数" in h.text for h in hits)


async def test_ingest_text_empty_raises(mock_embedder):
    svc, _ = _service(mock_embedder)
    with pytest.raises(EmptyDocument):
        await svc.ingest_text("u1", "空", "   ")


async def test_list_fragments_filter_by_category(mock_embedder):
    """按分类（文件名后缀推导）筛选知识库片段。"""
    svc, _ = _service(mock_embedder)
    await svc.ingest_text("u1", "a.txt", "文本内容一")     # 文本
    await svc.ingest_text("u1", "b.docx", "word 内容")     # Word
    await svc.ingest_text("u1", "c.txt", "文本内容二")     # 文本
    await svc.ingest_text("u1", "d.md", "# 标题")          # Markdown

    text_page = svc.list_fragments("u1", 1, 10, category="文本")
    assert text_page["total"] == 2
    assert all(it["category"] == "文本" for it in text_page["items"])

    word_page = svc.list_fragments("u1", 1, 10, category="Word")
    assert word_page["total"] == 1

    # 不传分类返回全部
    assert svc.list_fragments("u1", 1, 10)["total"] == 4


async def test_list_fragments_category_paginates(mock_embedder):
    svc, _ = _service(mock_embedder)
    for i in range(5):
        await svc.ingest_text("u1", f"n{i}.txt", f"文本 {i}")
    p1 = svc.list_fragments("u1", 1, 2, category="文本")
    p3 = svc.list_fragments("u1", 3, 2, category="文本")
    assert p1["total"] == 5 and len(p1["items"]) == 2
    assert len(p3["items"]) == 1                # 第 3 页余 1 条


# ---------- 重复导入去重 ----------

class _CountingEmbedder:
    """记录 embed 调用次数的假 embedder —— 去重必须发生在 embedding 之前（那步要花钱）。"""

    def __init__(self, dimension: int = 64):
        self.dimension = dimension
        self.calls = 0

    async def embed(self, texts):
        self.calls += 1
        return [[0.1] * self.dimension for _ in texts]


def _counting_service():
    store = SqliteVecBackend(":memory:", dimension=64)
    emb = _CountingEmbedder(64)
    mem = Memory(store, emb, chunk_size=1000, overlap=0)
    return KnowledgeService(mem, store, DocumentStore(":memory:")), store, emb


async def test_reingest_same_content_does_not_duplicate_vectors():
    """同一内容导入两次 → 向量库里只有一份，不产生完全重复的块。"""
    svc, store, emb = _counting_service()
    data = "光合作用把二氧化碳和水转化为氧气".encode()
    first = await svc.ingest("u1", "bio.txt", data)
    n_after_first = store.count_by_owner("u1", "knowledge")

    second = await svc.ingest("u1", "bio.txt", data)
    assert second["duplicate"] is True
    assert second["id"] == first["id"], "应回指原文档，而不是新建一个"
    assert store.count_by_owner("u1", "knowledge") == n_after_first, "向量不该变多"


async def test_dedup_happens_before_embedding():
    """去重要赶在 embedding 之前 —— 那一步打远端、花钱，重复内容不该付这笔。"""
    svc, _store, emb = _counting_service()
    data = "一段内容".encode()
    await svc.ingest("u1", "a.txt", data)
    assert emb.calls == 1
    await svc.ingest("u1", "a.txt", data)
    assert emb.calls == 1, "重复导入不该再打一次 embedding"


async def test_dedup_ignores_whitespace_only_differences():
    """改了换行/缩进重新导出 → 切出的块与向量实质相同，仍算重复。"""
    svc, store, _emb = _counting_service()
    await svc.ingest("u1", "a.txt", "第一行\n第二行".encode())
    n = store.count_by_owner("u1", "knowledge")
    dup = await svc.ingest("u1", "a.txt", "第一行\n\n  第二行  \n".encode())
    assert dup["duplicate"] is True
    assert store.count_by_owner("u1", "knowledge") == n


async def test_dedup_is_per_user():
    """去重按用户 —— 别人库里有同样内容，不影响我导入自己的那份。"""
    svc, store, _emb = _counting_service()
    data = "公共知识".encode()
    await svc.ingest("u1", "a.txt", data)
    mine = await svc.ingest("u2", "a.txt", data)
    assert mine["duplicate"] is False
    assert store.count_by_owner("u2", "knowledge") >= 1


async def test_different_content_same_filename_is_not_duplicate():
    """同名但内容改了 → 是新版本，照常导入（去重看内容，不看文件名）。"""
    svc, _store, emb = _counting_service()
    await svc.ingest("u1", "note.txt", "第一版内容".encode())
    second = await svc.ingest("u1", "note.txt", "改过的第二版内容".encode())
    assert second["duplicate"] is False
    assert emb.calls == 2


async def test_same_content_different_format_is_deduped():
    """同一份内容存成 .txt 与 .md 各传一次：字节不同，但正文相同 → 仍是重复。

    这正是按「解析后正文」而非「原始字节」算 hash 的理由。
    """
    svc, store, _emb = _counting_service()
    text = "纯文字内容没有任何标记"
    await svc.ingest("u1", "a.txt", text.encode())
    n = store.count_by_owner("u1", "knowledge")
    dup = await svc.ingest("u1", "a.md", text.encode())
    assert dup["duplicate"] is True
    assert store.count_by_owner("u1", "knowledge") == n


async def test_ingest_text_also_deduped(mock_embedder):
    """聊天里的 save_to_knowledge 走同一条去重路径。"""
    svc, _mem = _service(mock_embedder)
    first = await svc.ingest_text("u1", "笔记", "一段要保存的内容")
    second = await svc.ingest_text("u1", "笔记", "一段要保存的内容")
    assert second["duplicate"] is True and second["id"] == first["id"]


async def test_first_ingest_reports_not_duplicate(mock_embedder):
    svc, _mem = _service(mock_embedder)
    doc = await svc.ingest("u1", "a.txt", "全新内容".encode())
    assert doc["duplicate"] is False


async def test_legacy_rows_without_hash_do_not_match_each_other():
    """旧库文档的 content_hash 为 NULL；空 hash 之间不该互相认成重复。"""
    svc, _store, _emb = _counting_service()
    ds = svc._doc_store
    ds.create("u1", "old1", "old.txt", 10, ["c1"], "摘要")          # 不传 hash（模拟旧行）
    ds.create("u1", "old2", "old2.txt", 10, ["c2"], "摘要")
    assert ds.find_by_hash("u1", "") is None
    # 新导入照常，不会被旧行干扰
    doc = await svc.ingest("u1", "new.txt", "新内容".encode())
    assert doc["duplicate"] is False

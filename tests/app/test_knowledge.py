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

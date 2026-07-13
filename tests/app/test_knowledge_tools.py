# tests/app/test_knowledge_tools.py
from app.knowledge import KnowledgeService
from app.documents import DocumentStore
from app.tools.knowledge_tools import SaveToKnowledgeTool
from harness.memory.memory import Memory
from harness.memory.sqlite_backend import SqliteVecBackend


def _service(mock_embedder):
    store = SqliteVecBackend(":memory:", dimension=64)
    mem = Memory(store, mock_embedder(dimension=64), chunk_size=1000, overlap=0)
    return KnowledgeService(mem, store, DocumentStore(":memory:"))


async def test_save_tool_writes_to_user_knowledge(mock_embedder):
    svc = _service(mock_embedder)
    tool = SaveToKnowledgeTool(svc, "u1")
    out = await tool.run(tool.Params(title="AI 最新数据", text="2026 年发布了新一代模型"))
    assert "知识库" in out
    page = svc.list_fragments("u1", 1, 10)
    assert any(item["filename"] == "AI 最新数据" for item in page["items"])


async def test_save_tool_empty_content_reports_failure(mock_embedder):
    svc = _service(mock_embedder)
    tool = SaveToKnowledgeTool(svc, "u1")
    out = await tool.run(tool.Params(title="空", text="   "))
    assert "失败" in out
    assert svc.list_fragments("u1", 1, 10)["total"] == 0

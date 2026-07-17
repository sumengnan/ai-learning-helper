# tests/app/test_knowledge_tools.py
from app.knowledge import KnowledgeService, strip_markdown
from app.documents import DocumentStore
from app.tools.knowledge_tools import SaveToKnowledgeTool
from harness.memory.memory import Memory
from harness.memory.sqlite_backend import SqliteVecBackend


def _service(mock_embedder):
    store = SqliteVecBackend(":memory:", dimension=64)
    mem = Memory(store, mock_embedder(dimension=64), chunk_size=1000, overlap=0)
    return KnowledgeService(mem, store, DocumentStore(":memory:"))


# ---- strip_markdown ----

def test_strip_markdown_headings_inline_list_link():
    md = "## 小标题\n\n这是 **重点** 和 *斜体* 以及 `代码`。\n\n- 项目一\n- 项目二\n\n[官网](http://x.com)"
    out = strip_markdown(md)
    assert "#" not in out and "**" not in out and "`" not in out and "*" not in out
    for kw in ("小标题", "重点", "斜体", "代码", "项目一", "项目二", "官网"):
        assert kw in out
    assert "http://x.com" not in out          # 链接 URL 去掉，只留文字


def test_strip_markdown_code_fence_and_table():
    md = "```python\nprint(1)\n```\n\n| 名 | 分 |\n|---|---|\n| 甲 | 90 |"
    out = strip_markdown(md)
    assert "```" not in out and "print(1)" in out   # 围栏去掉、代码正文保留
    assert "|" not in out and "甲" in out and "90" in out  # 表格标记去掉、内容保留
    assert "分甲" not in out                         # 表头与数据行不粘连（换行保留）


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


def test_strip_citations_removes_inline_markers():
    from app.sources import strip_citations
    assert strip_citations("叶绿体中[1]，光照[2][3]。") == "叶绿体中，光照。"
    assert strip_citations("中 [1] 后") == "中 后"      # 角标连同前导空格一起去掉
    assert strip_citations("无角标文本") == "无角标文本"
    assert strip_citations("") == ""


async def test_save_tool_strips_citation_markers(mock_embedder):
    """保存到知识库时去掉正文里的来源角标 [1]，其余内容保留。"""
    svc = _service(mock_embedder)
    tool = SaveToKnowledgeTool(svc, "u1")
    await tool.run(tool.Params(
        title="笔记", text="光合作用发生在叶绿体中[1]，需要光照[2][3]。"))
    page = svc.list_fragments("u1", 1, 10)
    frag = svc.get_fragment("u1", page["items"][0]["id"])
    assert "[1]" not in frag["text"] and "[2]" not in frag["text"] and "[3]" not in frag["text"]
    assert "光合作用发生在叶绿体中" in frag["text"] and "需要光照" in frag["text"]


async def test_save_tool_strips_markdown_stores_plain_text(mock_embedder):
    svc = _service(mock_embedder)
    tool = SaveToKnowledgeTool(svc, "u1")
    await tool.run(tool.Params(
        title="笔记", text="## 概念\n\n**光合作用** 在叶绿体进行。\n\n- 要点一\n- 要点二"))
    page = svc.list_fragments("u1", 1, 10)
    frag = svc.get_fragment("u1", page["items"][0]["id"])
    assert "#" not in frag["text"] and "**" not in frag["text"]   # 无 markdown 标记
    for kw in ("概念", "光合作用", "要点一", "要点二"):
        assert kw in frag["text"]                                 # 内容保留

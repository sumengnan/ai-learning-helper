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
    assert "知识库" in out.text
    page = svc.list_fragments("u1", 1, 10)
    assert any(item["filename"] == "AI 最新数据" for item in page["items"])
    # 知识 id 只走 marker（交付门清理用），不进模型上下文——模型抄进正文对用户是乱码
    assert out.marker.startswith("〔知识ID:")
    assert out.marker.strip("〔〕").split(":")[1] not in out.text


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


# ---- 「保存说明」不得当作笔记正文入库 ----

# 用户实际遇到的那条：模型把本工具前一次的成功提示复述进了 text
_REAL_SAVE_REPORT = (
    "已根据提供的信息整理并保存了一份结构化的AI资讯笔记，标题为《2026世界人工智能大会及"
    "AI发展动态》。该笔记涵盖了大会的背景、关键点、现状/结论以及来源标注，适合存入知识库"
    "供后续参考。您可以在知识库菜单中查看该内容。")


async def test_save_report_is_rejected_not_ingested(mock_embedder):
    """回归：这段过程汇报被原样存进了知识库——检索时毫无价值，还挤占结果。"""
    svc = _service(mock_embedder)
    tool = SaveToKnowledgeTool(svc, "u1")
    out = await tool.run(tool.Params(title="最新AI资讯", text=_REAL_SAVE_REPORT))
    assert "保存失败" in out
    assert "笔记正文" in out                    # 报错要说清该传什么，模型才改得对
    assert svc.list_fragments("u1", 1, 10)["total"] == 0, "一个字都不该入库"


async def test_long_real_note_mentioning_knowledge_base_is_kept(mock_embedder):
    """反向护栏：真笔记若恰好谈到「知识库怎么用」也会命中关键词，绝不能误杀。

    错杀的代价是用户辛苦整理的笔记直接丢失，比留一条噪声严重得多——故只在短文本上判。
    """
    svc = _service(mock_embedder)
    tool = SaveToKnowledgeTool(svc, "u1")
    note = ("RAG 系统设计要点。背景：检索增强生成把外部资料引入模型上下文。"
            "关键点：切块粒度、向量化模型选择、精排与相关性下限。"
            "实践中我们把整理好的资料已存入知识库，可在知识库菜单查看并按需删除。"
            "结论：召回与精度需要按实际语料标定，不能照抄默认值。") * 3
    assert len(note) > 300
    out = await tool.run(tool.Params(title="RAG 笔记", text=note))
    assert "保存失败" not in out.text
    assert svc.list_fragments("u1", 1, 10)["total"] > 0


async def test_short_genuine_note_still_saves(mock_embedder):
    """短笔记本身没问题——只有「在描述保存动作」才拦。"""
    svc = _service(mock_embedder)
    tool = SaveToKnowledgeTool(svc, "u1")
    out = await tool.run(tool.Params(title="要点", text="Transformer 的核心是自注意力机制。"))
    assert "保存失败" not in out.text
    assert svc.list_fragments("u1", 1, 10)["total"] > 0

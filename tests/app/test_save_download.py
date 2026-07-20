import base64
import pytest
from app.downloads import DownloadStore
from app.tools.save_download import SaveDownloadTool


def _tool(tmp_path, max_mb=25):
    store = DownloadStore(str(tmp_path / "f"), ":memory:")
    return SaveDownloadTool(store, max_mb * 1024 * 1024, "u1"), store


@pytest.mark.asyncio
async def test_save_text(tmp_path):
    tool, store = _tool(tmp_path)
    out = await tool.run(tool.Params(filename="note.md", content="# 标题"))
    assert "已保存" in out.text
    lst = store.list("u1")
    assert len(lst) == 1 and lst[0]["filename"] == "note.md"
    assert lst[0]["content_type"] == "text/markdown"
    # 下载 id 只走 marker：供聊天页渲染下载按钮，且不会进模型上下文（模型抄进正文就露给用户了）
    assert out.marker == f"〔下载ID:{lst[0]['id']}〕"
    assert lst[0]["id"] not in out.text


@pytest.mark.asyncio
async def test_save_base64_image(tmp_path):
    tool, store = _tool(tmp_path)
    b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n fake png").decode()
    out = await tool.run(tool.Params(filename="chart.png", content=b64, encoding="base64"))
    assert "已保存" in out.text
    assert store.list("u1")[0]["content_type"] == "image/png"   # mimetypes 识别


@pytest.mark.asyncio
async def test_bad_base64_returns_error_no_store(tmp_path):
    tool, store = _tool(tmp_path)
    out = await tool.run(tool.Params(filename="x.png", content="不是base64!!!", encoding="base64"))
    assert "失败" in out and store.list("u1") == []              # 未落库


@pytest.mark.asyncio
async def test_oversize_returns_error_no_store(tmp_path):
    tool, store = _tool(tmp_path, max_mb=0)                     # 0MB 上限 → 任何内容都超
    out = await tool.run(tool.Params(filename="big.txt", content="hello"))
    assert "失败" in out and "上限" in out and store.list("u1") == []


# —— 文本内容不得套二进制文档的壳（模型爱写 .pdf，系统却没有渲染能力）——

@pytest.mark.asyncio
async def test_text_content_with_pdf_extension_rejected(tmp_path):
    """模型把 Markdown 存成 .pdf → 用户下载到打不开的坏文件。必须拦下且不落库。"""
    tool, store = _tool(tmp_path)
    out = await tool.run(tool.Params(filename="AI发展与应用总结.pdf", content="# 标题\n\n正文"))
    assert "失败" in out
    assert ".md" in out                      # 告诉模型改用什么
    assert store.list("u1") == []            # 未落库


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["报告.docx", "数据.xlsx", "讲义.pptx", "存档.zip"])
async def test_text_content_with_binary_doc_extensions_rejected(tmp_path, name):
    tool, store = _tool(tmp_path)
    out = await tool.run(tool.Params(filename=name, content="纯文本"))
    assert "失败" in out and store.list("u1") == []


@pytest.mark.asyncio
async def test_extension_check_is_case_insensitive(tmp_path):
    tool, store = _tool(tmp_path)
    out = await tool.run(tool.Params(filename="总结.PDF", content="正文"))
    assert "失败" in out and store.list("u1") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["笔记.md", "记录.txt", "页面.html", "表格.csv", "数据.json"])
async def test_text_friendly_extensions_still_allowed(tmp_path, name):
    """文本格式一律放行——本修复只拦「文本内容套二进制文档壳」这一种情况。"""
    tool, store = _tool(tmp_path)
    out = await tool.run(tool.Params(filename=name, content="内容"))
    assert "已保存" in out and len(store.list("u1")) == 1


@pytest.mark.asyncio
async def test_base64_pdf_still_allowed(tmp_path):
    """encoding=base64 时模型提供的是真二进制，不该被这道校验误伤。"""
    tool, store = _tool(tmp_path)
    b64 = base64.b64encode(b"%PDF-1.4 fake").decode()
    out = await tool.run(tool.Params(filename="真报告.pdf", content=b64, encoding="base64"))
    assert "已保存" in out
    assert store.list("u1")[0]["content_type"] == "application/pdf"


def test_description_states_supported_formats():
    """description 要写清能产出什么格式，否则模型只能靠猜（这正是 .pdf 的来源）。"""
    desc = SaveDownloadTool.description
    assert ".md" in desc
    assert "PDF" in desc or "pdf" in desc      # 明确说明不支持


def test_description_does_not_trigger_on_content_shaping():
    """「整理成笔记」不能当触发语——它精准命中「内容加工」类子步，导致该步也存一份文件。

    实例：计划第2步「把检索到的内容整理成结构化的学习笔记」、第3步「保存为可供下载的
    成品文件」，两步都调了 save_download，用户下载区出现两份重复文件。
    触发语只保留明确要文件的说法（导出/存成文件/供下载）。
    """
    desc = SaveDownloadTool.description
    assert "整理成笔记" not in desc
    assert "导出" in desc and "下载" in desc


@pytest.mark.asyncio
async def test_strips_leaked_step_markers(tmp_path):
    """存成品文件前剥掉内部步骤标记 [sN]——它对用户毫无意义，纯属管道残留。"""
    tool, store = _tool(tmp_path)
    await tool.run(tool.Params(filename="笔记.md", content="[s2] # 标题\n\n正文[s10]"))
    did = store.list("u1")[0]["id"]
    text = open(store.path(did), encoding="utf-8").read()
    assert "[s2]" not in text and "[s10]" not in text
    assert "# 标题" in text and "正文" in text


@pytest.mark.asyncio
async def test_strips_numeric_citations(tmp_path):
    """数字角标 [n] 脱离原对话后没有指向，成品文件里是纯噪声，落盘前剥掉。"""
    tool, store = _tool(tmp_path)
    await tool.run(tool.Params(filename="笔记.md", content="光合作用发生在叶绿体中[1]，另见[12]。"))
    text = open(store.path(store.list("u1")[0]["id"]), encoding="utf-8").read()
    assert "[1]" not in text and "[12]" not in text
    assert "光合作用发生在叶绿体中" in text


@pytest.mark.asyncio
async def test_does_not_corrupt_code_indexing(tmp_path):
    """代码块里的 arr[1] 不是角标——剥离必须跳过代码，否则导出的代码笔记会被改坏。"""
    tool, store = _tool(tmp_path)
    content = (
        "讲解见下[1]。\n\n"
        "```python\n"
        "arr = [10, 20]\n"
        "print(arr[0], arr[1])\n"
        "```\n\n"
        "行内的 `nums[2]` 也要保住。\n"
    )
    await tool.run(tool.Params(filename="代码笔记.md", content=content))
    text = open(store.path(store.list("u1")[0]["id"]), encoding="utf-8").read()
    assert "arr[0], arr[1]" in text        # 围栏代码块原样保留
    assert "`nums[2]`" in text             # 行内代码原样保留
    assert "讲解见下。" in text             # 正文里的角标仍被剥掉

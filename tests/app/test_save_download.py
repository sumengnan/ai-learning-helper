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

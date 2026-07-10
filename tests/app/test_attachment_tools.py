import base64

import pytest

from app.attachments import AttachmentStore
from app.tools.attachment_tools import ListAttachmentsTool, ReadAttachmentTool
from harness.types import Message, Role, ToolOutput

# 1x1 透明 PNG
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+P+/HgAFhAJ/wlseKgAAAABJRU5ErkJggg==")


def _store(tmp_path):
    return AttachmentStore(str(tmp_path / "att"), ":memory:")


async def test_list_attachments(tmp_path):
    store = _store(tmp_path)
    store.create("u", "c1", "a.txt", b"hi", "text/plain")
    out = await ListAttachmentsTool(store, "u", "c1").run(ListAttachmentsTool.Params())
    assert "a.txt" in out
    # 空会话
    empty = await ListAttachmentsTool(store, "u", "c2").run(ListAttachmentsTool.Params())
    assert "没有" in empty


async def test_read_text_attachment(tmp_path):
    store = _store(tmp_path)
    rec = store.create("u", "c1", "notes.txt", "重要内容".encode("utf-8"), "text/plain")
    tool = ReadAttachmentTool(store, "u", "c1", 5 * 1024 * 1024)
    out = await tool.run(ReadAttachmentTool.Params(attachment_id=rec["id"]))
    assert "重要内容" in out


async def test_read_image_returns_vision_follow_up(tmp_path):
    store = _store(tmp_path)
    rec = store.create("u", "c1", "pic.png", _PNG, "image/png")
    tool = ReadAttachmentTool(store, "u", "c1", 5 * 1024 * 1024)
    out = await tool.run(ReadAttachmentTool.Params(attachment_id=rec["id"]))
    assert isinstance(out, ToolOutput)
    assert len(out.follow_up) == 1
    fm = out.follow_up[0]
    assert fm.role == Role.USER and isinstance(fm.content, list)
    parts = {p["type"] for p in fm.content}
    assert "image_url" in parts
    img = next(p for p in fm.content if p["type"] == "image_url")
    assert img["image_url"]["url"].startswith("data:image/png;base64,")


async def test_read_oversize_image_points_to_sandbox(tmp_path):
    store = _store(tmp_path)
    rec = store.create("u", "c1", "big.png", _PNG, "image/png")
    tool = ReadAttachmentTool(store, "u", "c1", 1)  # 阈值 1 字节 → 视为过大
    out = await tool.run(ReadAttachmentTool.Params(attachment_id=rec["id"]))
    assert isinstance(out, str) and "/workspace/uploads/big.png" in out


async def test_read_binary_points_to_sandbox(tmp_path):
    store = _store(tmp_path)
    rec = store.create("u", "c1", "app.bin", b"\x00\x01\x02", "application/octet-stream")
    tool = ReadAttachmentTool(store, "u", "c1", 5 * 1024 * 1024)
    out = await tool.run(ReadAttachmentTool.Params(attachment_id=rec["id"]))
    assert "/workspace/uploads/app.bin" in out


async def test_read_missing_attachment(tmp_path):
    store = _store(tmp_path)
    tool = ReadAttachmentTool(store, "u", "c1", 5 * 1024 * 1024)
    out = await tool.run(ReadAttachmentTool.Params(attachment_id="nope"))
    assert "未找到" in out


def test_message_multimodal_to_openai_passthrough():
    content = [{"type": "text", "text": "hi"},
               {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]
    m = Message(role=Role.USER, content=content)
    d = m.to_openai()
    assert d["role"] == "user" and d["content"] == content

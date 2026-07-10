# app/tools/attachment_tools.py
from __future__ import annotations

import base64

from pydantic import BaseModel

from harness.tools.base import Tool
from harness.types import Message, Role, ToolOutput

from ..parsing import ParseError, UnsupportedFormat, parse_file

_TEXT_EXTS = ("txt", "md", "pdf", "docx")


def _ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""


class ListAttachmentsTool(Tool):
    name = "list_attachments"
    description = (
        "列出本次对话中用户上传的附件（id、文件名、类型）。"
        "需要某个附件的内容时，再用 read_attachment(attachment_id) 获取。")

    class Params(BaseModel):
        pass

    def __init__(self, store, user_id: str, conv_id: str) -> None:
        self._store = store
        self._uid = user_id
        self._cid = conv_id

    async def run(self, params: "ListAttachmentsTool.Params") -> str:
        metas = self._store.list_conv(self._uid, self._cid)
        if not metas:
            return "当前对话没有用户上传的附件。"
        lines = [f"- {m['id']}｜{m['filename']}｜{m['content_type']}｜{m['size']} 字节"
                 for m in metas]
        return "本次对话的附件：\n" + "\n".join(lines)


class ReadAttachmentTool(Tool):
    name = "read_attachment"
    description = (
        "读取某个上传附件的内容。txt/md/pdf/docx 返回抽取的文本；"
        "图片会作为视觉内容加载供你查看；其它二进制文件已放入沙箱 /workspace/uploads/，"
        "请用 run_python/run_shell 处理。先用 list_attachments 获取 attachment_id。")

    class Params(BaseModel):
        attachment_id: str

    def __init__(self, store, user_id: str, conv_id: str, vision_max_bytes: int) -> None:
        self._store = store
        self._uid = user_id
        self._cid = conv_id
        self._vision_max = vision_max_bytes

    async def run(self, params: "ReadAttachmentTool.Params") -> "str | ToolOutput":
        rec = self._store.get(self._uid, params.attachment_id)
        if rec is None:
            return "未找到该附件（attachment_id 有误，或不属于本对话/用户）。"
        filename, content_type = rec["filename"], rec["content_type"]
        data = self._store.bytes(params.attachment_id)
        ext = _ext(filename)
        sandbox_path = f"/workspace/uploads/{filename}"

        # 图片：作为视觉内容注入一条后续 user 消息，让模型直接“看”
        if content_type.startswith("image/"):
            if len(data) > self._vision_max:
                return (f"图片「{filename}」过大（{len(data)} 字节），无法直接查看。"
                        f"它已在沙箱 {sandbox_path}，可用 run_python（如 PIL）处理。")
            b64 = base64.b64encode(data).decode()
            url = f"data:{content_type};base64,{b64}"
            follow = Message(role=Role.USER, content=[
                {"type": "text", "text": f"（附件图片 {filename}）"},
                {"type": "image_url", "image_url": {"url": url}},
            ])
            return ToolOutput(text=f"已加载图片「{filename}」，见下条消息。", follow_up=[follow])

        # 文本类：抽取文本
        if ext in _TEXT_EXTS:
            try:
                text = parse_file(filename, data)
            except (UnsupportedFormat, ParseError) as e:
                return f"读取「{filename}」失败：{e}。它已在沙箱 {sandbox_path}。"
            return f"「{filename}」内容：\n{text}" if text.strip() else \
                f"「{filename}」解析为空。它已在沙箱 {sandbox_path}。"

        # 其它二进制：指向沙箱
        return (f"「{filename}」（{content_type}）是二进制文件，无法作为文本读取，"
                f"已放入沙箱 {sandbox_path}，请用 run_python/run_shell 处理。")

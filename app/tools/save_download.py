# app/tools/save_download.py
from __future__ import annotations

import base64
import binascii
import mimetypes

from pydantic import BaseModel

from harness.tools.base import Tool
from harness.types import ToolOutput


class SaveDownloadTool(Tool):
    name = "save_download"
    description = (
        "把整理好的内容生成为「给用户的成品文件」供查看/下载——学习笔记、总结、报告、导出、图表等。"
        "这是给用户的最终产物、文件（不进知识库检索）。用户说「整理成笔记 / 导出 / 存成文件」用这个。"
        "content 为文本内容；若要保存图片等二进制，先把它 base64 编码并令 encoding=base64。")

    class Params(BaseModel):
        filename: str
        content: str
        encoding: str = "text"        # "text" | "base64"

    def __init__(self, download_store, max_bytes: int, user_id: str | None = None) -> None:
        self._store = download_store
        self._max = max_bytes
        self._uid = user_id

    async def run(self, params: "SaveDownloadTool.Params") -> "str | ToolOutput":
        if params.encoding == "base64":
            try:
                data = base64.b64decode(params.content, validate=True)
            except (binascii.Error, ValueError):
                return "保存失败：内容不是合法 base64。"
        else:
            data = params.content.encode("utf-8")
        if len(data) > self._max:
            return f"保存失败：超过 {self._max // (1024 * 1024)}MB 上限。"
        content_type = mimetypes.guess_type(params.filename)[0] or "application/octet-stream"
        rec = self._store.create(self._uid, params.filename, data, content_type)
        # 末尾带机读标记〔下载ID:...〕：前端据此在该条消息下方渲染下载按钮（会剥离不展示给用户）。
        # 走 marker 而非拼进 text——它不进模型上下文，模型看不见就不会把这串 id 抄进回复正文。
        return ToolOutput(
            text=(f"已保存到下载区：{rec['filename']}（{rec['size']} 字节），"
                  f"用户可在该条消息下方点按钮下载。"),
            marker=f"〔下载ID:{rec['id']}〕")

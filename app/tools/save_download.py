# app/tools/save_download.py
from __future__ import annotations

import base64
import binascii
import mimetypes

from pydantic import BaseModel

from harness.tools.base import Tool


class SaveDownloadTool(Tool):
    name = "save_download"
    description = (
        "把整理好的内容保存为可下载文件（笔记/导出/图表）。"
        "content 为文本内容；若要保存图片等二进制，先把它 base64 编码并令 encoding=base64。")

    class Params(BaseModel):
        filename: str
        content: str
        encoding: str = "text"        # "text" | "base64"

    def __init__(self, download_store, max_bytes: int, user_id: str | None = None) -> None:
        self._store = download_store
        self._max = max_bytes
        self._uid = user_id

    async def run(self, params: "SaveDownloadTool.Params") -> str:
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
        return f"已保存到下载区：{rec['filename']}（{rec['size']} 字节）。"

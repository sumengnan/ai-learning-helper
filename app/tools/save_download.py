# app/tools/save_download.py
from __future__ import annotations

import base64
import binascii
import mimetypes

from pydantic import BaseModel

from harness.tools.base import Tool


# 紧跟在结果里、机读标记之前的就近提醒。模型手上没有任何可用的下载地址，出于「给个入口」
# 的好意就会自己编一个（实测编出指向 `#` 的 markdown 链接，点了停在当前页），用户看到的是
# 死链。界面本就会据〔下载ID:...〕在该条消息下方渲染真正的下载按钮，正文里不需要也不该有链接。
_NO_LINK_HINT = (
    "（界面已在本条消息下方自动显示下载按钮。"
    "不要在回答里写下载链接、URL 或 markdown 链接——你没有可用的地址，写出来必然是死链；"
    "只需说明文件已生成即可。）")


class SaveDownloadTool(Tool):
    name = "save_download"
    description = (
        "把整理好的内容生成为「给用户的成品文件」供查看/下载——学习笔记、总结、报告、导出、图表等。"
        "这是给用户的最终产物、文件（不进知识库检索）。用户说「整理成笔记 / 导出 / 存成文件」用这个。"
        "content 为文本内容；若要保存图片等二进制，先把它 base64 编码并令 encoding=base64。"
        "注意：界面会自动在该条消息下方显示下载按钮，你不要在回答里写下载链接或 URL。")

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
        # 末尾带机读标记〔下载ID:...〕：前端据此在该条消息下方渲染下载按钮（会剥离不展示给用户）
        return (f"已保存到下载区：{rec['filename']}（{rec['size']} 字节）。{_NO_LINK_HINT}"
                f"〔下载ID:{rec['id']}〕")

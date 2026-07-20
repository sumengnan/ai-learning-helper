# app/tools/save_download.py
from __future__ import annotations

import base64
import binascii
import mimetypes
import os

from pydantic import BaseModel

from harness.tools.base import Tool

# 本系统只把 content 原样写成字节，没有任何排版/渲染能力（依赖里没有 reportlab、
# weasyprint、pandoc 之流；python-docx 只用于「读」上传附件）。文本内容配上这些扩展名
# 就是给用户一个打不开的坏文件——mimetypes 还会按文件名把它标成 application/pdf。
_BINARY_DOC_EXTS = frozenset({
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "zip", "rar", "7z", "epub", "rtf",
})
_TEXT_EXTS_HINT = ".md、.txt、.html、.csv、.json"


class SaveDownloadTool(Tool):
    name = "save_download"
    description = (
        "把已经写好的内容落成「给用户的成品文件」供下载——学习笔记、总结、报告、图表等。"
        "这是给用户的最终产物、文件（不进知识库检索）。"
        # 触发语只认「明确要文件」的说法。曾用「整理成笔记」当触发语，结果精准命中了
        # 「把资料整理成学习笔记」这类内容加工子步——该步和后面真正的保存步各存一份，
        # 用户下载区出现两份重复文件。内容加工不产生文件，只有明说要文件时才调本工具。
        "仅在用户明确要文件时调用：说「导出 / 存成文件 / 生成可下载的文件」等。"
        "「整理一下 / 写成笔记 / 总结成报告」只是要内容，直接把内容写在答复里，不要调本工具。"
        "同一份内容只存一次——若前置步骤已保存过，不要再存第二份。"
        f"content 为文本内容，扩展名只能用文本格式（{_TEXT_EXTS_HINT}）；"
        "本系统不能生成 PDF/Word/Excel/PPT，用户即使说「导出 PDF」也要存成 .md 并在答复里说明。"
        "若要保存图片等二进制，先把它 base64 编码并令 encoding=base64。")

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
            # base64 走的是模型自备的真二进制，不设限；只拦「文本内容套二进制文档壳」
            ext = os.path.splitext(params.filename)[1].lstrip(".").lower()
            if ext in _BINARY_DOC_EXTS:
                return (f"保存失败：本系统不能生成 {ext.upper()} 文件，只能写文本。"
                        f"请把 filename 换成文本扩展名（{_TEXT_EXTS_HINT}）重试，"
                        "并在给用户的答复里说明格式已改。")
            data = params.content.encode("utf-8")
        if len(data) > self._max:
            return f"保存失败：超过 {self._max // (1024 * 1024)}MB 上限。"
        content_type = mimetypes.guess_type(params.filename)[0] or "application/octet-stream"
        rec = self._store.create(self._uid, params.filename, data, content_type)
        # 末尾带机读标记〔下载ID:...〕：前端据此在该条消息下方渲染下载按钮（会剥离不展示给用户）
        return (f"已保存到下载区：{rec['filename']}（{rec['size']} 字节）。"
                f"〔下载ID:{rec['id']}〕")

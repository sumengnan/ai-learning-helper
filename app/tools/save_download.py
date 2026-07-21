# app/tools/save_download.py
from __future__ import annotations

import base64
import binascii
import mimetypes
import os

from pydantic import BaseModel

from harness.tools.base import Tool
from harness.types import ToolOutput

from ..sources import strip_citations_outside_code, strip_step_markers

# 本系统只把 content 原样写成字节，没有任何排版/渲染能力（依赖里没有 reportlab、
# weasyprint、pandoc 之流；python-docx 只用于「读」上传附件）。文本内容配上这些扩展名
# 就是给用户一个打不开的坏文件——mimetypes 还会按文件名把它标成 application/pdf。
_BINARY_DOC_EXTS = frozenset({
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "zip", "rar", "7z", "epub", "rtf",
})
_TEXT_EXTS_HINT = ".md、.txt、.html、.csv、.json"


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
        "把已经写好的内容落成「给用户的成品文件」供下载——学习笔记、总结、报告、图表等。"
        "这是给用户的最终产物、文件（不进知识库检索）。"
        # 触发语只认「明确要文件」的说法。曾用「整理成笔记」当触发语，结果精准命中了
        # 「把资料整理成学习笔记」这类内容加工子步——该步和后面真正的保存步各存一份，
        # 用户下载区出现两份重复文件。内容加工不产生文件，只有明说要文件时才调本工具。
        "仅在用户明确要文件时调用：说「导出 / 存成文件 / 生成可下载的文件」等。"
        "「整理一下 / 写成笔记 / 总结成报告」只是要内容，直接把内容写在答复里，不要调本工具。"
        "同一份内容只存一次——若前置步骤已保存过，不要再存第二份。"
        # 命名此前完全没约束，模型爱叫什么叫什么，于是下载区里一堆「笔记.md」「总结.md」，
        # 隔天回来根本认不出哪个是哪个。同名不同内容还会各存一条（见 DownloadStore.create），
        # 列表里并排两个「学习笔记.md」，只能靠大小和时间猜。
        "filename 要能脱离当前对话独立辨认：写清主题，别用「笔记 / 总结 / 报告 / 文档」这类"
        "泛称当全名，例如「Transformer注意力机制学习笔记.md」而非「学习笔记.md」。"
        "不要在文件名里写日期或版本号——系统已记录保存时间，写了只会和真实时间对不上。"
        f"content 为文本内容，扩展名只能用文本格式（{_TEXT_EXTS_HINT}）；"
        "本系统不能生成 PDF/Word/Excel/PPT，用户即使说「导出 PDF」也要存成 .md 并在答复里说明。"
        "若要保存图片等二进制，先把它 base64 编码并令 encoding=base64。"
        "注意：界面会自动在该条消息下方显示下载按钮，你不要在回答里写下载链接或 URL。")

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
            # base64 走的是模型自备的真二进制，不设限；只拦「文本内容套二进制文档壳」
            ext = os.path.splitext(params.filename)[1].lstrip(".").lower()
            if ext in _BINARY_DOC_EXTS:
                return (f"保存失败：本系统不能生成 {ext.upper()} 文件，只能写文本。"
                        f"请把 filename 换成文本扩展名（{_TEXT_EXTS_HINT}）重试，"
                        "并在给用户的答复里说明格式已改。")
            # 交付给用户的成品文件不带管道残留：内部步骤标记 [sN] 与来源角标 [n] 都剥掉
            # （角标脱离原对话后没有指向，与 save_to_knowledge 的处理一致）。代码块除外。
            clean = strip_citations_outside_code(strip_step_markers(params.content))
            data = clean.encode("utf-8")
        if len(data) > self._max:
            return f"保存失败：超过 {self._max // (1024 * 1024)}MB 上限。"
        content_type = mimetypes.guess_type(params.filename)[0] or "application/octet-stream"
        rec = self._store.create(self._uid, params.filename, data, content_type)
        # 末尾带机读标记〔下载ID:...〕：前端据此在该条消息下方渲染下载按钮（会剥离不展示给用户）。
        # 走 marker 而非拼进 text——它不进模型上下文，模型看不见就不会把这串 id 抄进回复正文。
        return ToolOutput(
            text=(f"已保存到下载区：{rec['filename']}（{rec['size']} 字节），"
                  f"用户可在该条消息下方点按钮下载。{_NO_LINK_HINT}"),
            marker=f"〔下载ID:{rec['id']}〕")

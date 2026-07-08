# app/parsing.py
from __future__ import annotations

import io


class UnsupportedFormat(Exception):
    pass


class ParseError(Exception):
    """文件格式受支持但内容损坏/无法解析。"""


def parse_file(filename: str, data: bytes) -> str:
    name = filename or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in ("txt", "md"):
        return data.decode("utf-8", errors="replace")
    if ext == "pdf":
        from pypdf import PdfReader
        try:
            reader = PdfReader(io.BytesIO(data))
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as e:  # pypdf 对损坏文件抛多种异常
            raise ParseError(f"PDF 解析失败：{e}") from e
    if ext == "docx":
        import docx
        try:
            document = docx.Document(io.BytesIO(data))
            return "\n".join(p.text for p in document.paragraphs)
        except Exception as e:  # python-docx 对非 zip/损坏文件抛 PackageNotFoundError 等
            raise ParseError(f"docx 解析失败：{e}") from e
    raise UnsupportedFormat(ext or name)

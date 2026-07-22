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
            return _docx_text(document)
        except Exception as e:  # python-docx 对非 zip/损坏文件抛 PackageNotFoundError 等
            raise ParseError(f"docx 解析失败：{e}") from e
    raise UnsupportedFormat(ext or name)


def _docx_text(document) -> str:
    """按文档原始顺序抽出段落与表格文字（含单元格内的嵌套表格、内容控件包裹的内容）。

    只取 document.paragraphs 会漏掉表格里的全部文字（那些在 document.tables 里，是一条
    独立的列表）——简历、报表、对照表这类 docx 大量用表格，漏了它们上传后知识库检索不到。
    而 paragraphs 与 tables 是两个平行列表，简单拼接会打乱正文与表格的先后；故遍历
    容器的 XML 子元素按真实顺序取。

    须递归下钻，否则仍有静默丢失（实测 python-docx 1.2.0）：
    - 单元格里可以再嵌一张表（复杂报表），cell.text 只读单元格段落、不读嵌套表；
    - 内容控件 w:sdt（表单/模板的下拉、日期框）在 body 级是独立元素，把 w:p/w:tbl 包在
      w:sdtContent 里，平铺遍历会整段跳过。
    故对「段落 / 表格 / sdt」三类分别处理，表格单元格与 sdt 内容都回到同一套遍历。
    不覆盖页眉页脚、脚注、文本框：它们在独立 XML part 或 drawing 里，且多为页码/水印/
    装饰性标注，抽进来对知识库检索是噪声。
    """
    return "\n".join(_iter_block_text(document.element.body, document))


def _iter_block_text(container, document) -> list[str]:
    """按 XML 顺序抽出一个容器（body / 单元格 / sdtContent）下的块级文字。"""
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    parts: list[str] = []
    for child in container.iterchildren():
        if child.tag == qn("w:p"):
            parts.append(Paragraph(child, document).text)
        elif child.tag == qn("w:tbl"):
            for row in Table(child, document).rows:
                # 每个单元格递归下钻：单元格里可能又是段落 + 嵌套表格
                cells = ["\n".join(_iter_block_text(c._tc, document)) for c in row.cells]
                parts.append("\t".join(cells))
        elif child.tag == qn("w:sdt"):
            # 内容控件：真正的内容在 w:sdtContent 里，对它再走一遍同样的遍历
            content = child.find(qn("w:sdtContent"))
            if content is not None:
                parts += _iter_block_text(content, document)
    return parts

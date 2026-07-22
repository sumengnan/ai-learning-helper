import io
import pytest
from app.parsing import parse_file, ParseError, UnsupportedFormat


def test_parse_txt_and_md():
    assert parse_file("a.txt", "你好\n世界".encode()) == "你好\n世界"
    assert "标题" in parse_file("b.md", "# 标题".encode())


def test_parse_docx_roundtrip():
    import docx
    d = docx.Document()
    d.add_paragraph("第一段内容")
    d.add_paragraph("第二段内容")
    buf = io.BytesIO(); d.save(buf)
    text = parse_file("x.docx", buf.getvalue())
    assert "第一段内容" in text and "第二段内容" in text


def test_parse_docx_includes_table_cells():
    """回归：docx 只取 paragraphs 会漏掉表格里的全部文字（那些在 tables 里）。

    简历、报表、对照表大量用表格，漏了它们上传后知识库检索不到——这是静默的数据丢失。
    """
    import docx
    d = docx.Document()
    d.add_paragraph("正文开头")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "姓名"; t.cell(0, 1).text = "年龄"
    t.cell(1, 0).text = "张三"; t.cell(1, 1).text = "28"
    buf = io.BytesIO(); d.save(buf)
    text = parse_file("resume.docx", buf.getvalue())
    for cell in ("姓名", "年龄", "张三", "28"):
        assert cell in text, f"表格单元格「{cell}」丢失"


def test_parse_docx_preserves_paragraph_table_order():
    """段落与表格在 python-docx 里是两条平行列表，简单拼接会打乱先后。
    须按文档真实顺序输出：正文 → 表格 → 正文。"""
    import docx
    d = docx.Document()
    d.add_paragraph("前言段落")
    t = d.add_table(rows=1, cols=1)
    t.cell(0, 0).text = "表格内容"
    d.add_paragraph("结尾段落")
    buf = io.BytesIO(); d.save(buf)
    text = parse_file("x.docx", buf.getvalue())
    assert text.index("前言段落") < text.index("表格内容") < text.index("结尾段落")


def test_unsupported_format_raises():
    with pytest.raises(UnsupportedFormat):
        parse_file("x.pptx", b"data")


def test_none_filename_raises_unsupported():
    # UploadFile.filename 可能为 None，应干净抛 UnsupportedFormat 而非 TypeError
    with pytest.raises(UnsupportedFormat):
        parse_file(None, b"data")


def test_corrupt_pdf_raises_parse_error():
    with pytest.raises(ParseError):
        parse_file("broken.pdf", b"not a real pdf")


def test_corrupt_docx_raises_parse_error():
    with pytest.raises(ParseError):
        parse_file("broken.docx", b"not a real docx")

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

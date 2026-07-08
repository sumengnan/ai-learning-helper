import io
import pytest
from app.parsing import parse_file, UnsupportedFormat


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

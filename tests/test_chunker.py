import pytest
from harness.memory.chunker import chunk


def test_short_text_not_split():
    assert chunk("hi", 1000, 200) == ["hi"]


def test_empty_text():
    assert chunk("   ", 1000, 200) == []


def test_long_text_split_with_overlap():
    text = "".join(str(i % 10) for i in range(2500))  # 2500 字符
    chunks = chunk(text, 1000, 200)
    assert len(chunks) == 4
    assert chunks[0] == text[0:1000]
    assert chunks[1] == text[800:1800]         # 步长 800，与前块重叠 200
    assert chunks[1][:200] == chunks[0][-200:]  # 重叠内容一致


def test_invalid_params():
    with pytest.raises(ValueError):
        chunk("x", 0, 0)
    with pytest.raises(ValueError):
        chunk("x", 100, 100)

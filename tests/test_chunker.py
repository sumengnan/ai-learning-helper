import pytest
from harness.memory.chunker import chunk


def test_short_text_not_split():
    assert chunk("hi", 1000, 200) == ["hi"]


def test_empty_text():
    assert chunk("   ", 1000, 200) == []


def test_long_text_split_with_overlap():
    text = "".join(str(i % 10) for i in range(2500))  # 2500 字符
    chunks = chunk(text, 1000, 200)
    # 起点 0/800/1600/2400 共 4 块，末块 text[2400:2500] 被 text[1600:2500] 完整覆盖 → 丢弃
    assert len(chunks) == 3
    assert chunks[0] == text[0:1000]
    assert chunks[1] == text[800:1800]         # 步长 800，与前块重叠 200
    assert chunks[1][:200] == chunks[0][-200:]  # 重叠内容一致


def test_invalid_params():
    with pytest.raises(ValueError):
        chunk("x", 0, 0)
    with pytest.raises(ValueError):
        chunk("x", 100, 100)


def test_redundant_tail_chunk_dropped_when_overlapped():
    # "a"*1601 → [1000, 801, 1]，末块 1 字符完全被前块重叠区覆盖 → 应丢弃
    chunks = chunk("a" * 1601, 1000, 200)
    assert len(chunks) == 2
    assert len(chunks[-1]) > 200          # 末块不再是那个 1 字符冗余块


def test_real_small_tail_kept_when_no_overlap():
    # overlap=0 时真实小尾块必须保留（不被误删）
    chunks = chunk("a" * 201, 100, 0)
    assert len(chunks) == 3
    assert chunks[-1] == "a"

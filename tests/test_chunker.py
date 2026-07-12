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


def test_splits_on_sentence_boundary_not_midsentence():
    # 多句中文（每句以 。 结尾），切块应落在句子边界后，不把某句切成半截
    text = "".join(f"第{i}句话到此为止结束了呢。" for i in range(20))
    chunks = chunk(text, 40, 8)
    assert len(chunks) > 1
    # 除末块外，每块都应在句子边界（。）后结束
    for c in chunks[:-1]:
        assert c.endswith("。")
    # 全文可由各块拼接覆盖（不丢内容）
    assert "".join(chunks).replace("。", "").count("第") >= text.count("第")


def test_falls_back_to_hard_cut_without_boundaries():
    # 无任何边界字符的连续串 → 退回按 chunk_size 硬切（保持旧行为）
    text = "a" * 250
    chunks = chunk(text, 100, 20)
    assert chunks[0] == text[0:100]
    assert chunks[1] == text[80:180]

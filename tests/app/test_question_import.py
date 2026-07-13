import pytest

from app.question_import import QuestionImporter
from app.questions import QuestionStore

GOOD = ('[{"type":"single","stem":"1+1=?","options":["1","2"],"answer":1,'
        '"explanation":"二"},'
        '{"type":"single","stem":"1+1=?","options":["1","2"],"answer":1},'   # 批内重复
        '{"type":"single","stem":"越界","options":["1","2"],"answer":9},'    # 非法
        '{"type":"truefalse","stem":"天是蓝的","options":null,"answer":true}]')


def _importer(raw):
    async def complete(system, user):
        return raw
    return QuestionImporter(complete, QuestionStore(":memory:"))


@pytest.mark.asyncio
async def test_import_extracts_dedups_and_counts():
    imp = _importer(GOOD)
    res = await imp.import_text("u1", "题库.txt", "任意文本")
    assert res == {"imported": 2, "skipped_invalid": 1, "skipped_duplicate": 1}
    saved = imp._store.list("u1")
    assert {q["source"] for q in saved} == {"题库.txt"}     # source=文件名


@pytest.mark.asyncio
async def test_import_dedups_against_existing_bank():
    imp = _importer(GOOD)
    imp._store.create("u1", {"type": "single", "stem": "1+1=?",
                             "options": ["1", "2"], "answer": 1})
    res = await imp.import_text("u1", "f.txt", "x")
    assert res["imported"] == 1 and res["skipped_duplicate"] == 2   # 单选1+1对库重复


@pytest.mark.asyncio
async def test_import_bad_json_returns_zero():
    res = await _importer("这不是JSON").import_text("u1", "f.txt", "x")
    assert res == {"imported": 0, "skipped_invalid": 0, "skipped_duplicate": 0}


@pytest.mark.asyncio
async def test_short_text_single_call():
    """短文本走单次调用（不分块），行为与原先一致。"""
    calls = []

    async def complete(system, user):
        calls.append(user)
        return GOOD
    imp = QuestionImporter(complete, QuestionStore(":memory:"), chunk_chars=1800)
    await imp.import_text("u1", "f.txt", "很短的文本")
    assert len(calls) == 1


# 带题号的多题文本（题号行即可切边界），每题一段
_BANK_TEXT = "\n".join(f"{i}. 第{i}题的题干内容占位补足长度xxxxxxxxxx" for i in range(1, 51))


@pytest.mark.asyncio
async def test_long_text_splits_into_parallel_calls():
    """长文本按题目边界切成多块并行抽取，结果合并入库。"""
    calls = []

    async def complete(system, user):
        idx = len(calls) + 1
        calls.append(user)
        return f'[{{"type":"short","stem":"块{idx}题","answer":"a{idx}"}}]'
    imp = QuestionImporter(complete, QuestionStore(":memory:"), chunk_chars=400)
    res = await imp.import_text("u1", "f.txt", _BANK_TEXT)
    assert len(calls) > 1                          # 确实分了多块
    assert res["imported"] == len(calls)           # 每块一题，全部合并入库


@pytest.mark.asyncio
async def test_split_never_cuts_a_question():
    """分块只落在题目边界：每块都以题号行开头，整题不被拆开。"""
    from app.question_import import _split_text
    chunks = _split_text(_BANK_TEXT, 400)
    assert len(chunks) > 1
    for c in chunks:
        assert c.lstrip().split(".", 1)[0].strip().isdigit()   # 每块首行是题号


@pytest.mark.asyncio
async def test_no_boundary_text_stays_single_chunk():
    """无空行、无题号的文本无法安全切分 → 单块（不丢题，安全退化）。"""
    from app.question_import import _split_text
    text = "".join("很长的一段没有任何边界的连续文本" for _ in range(200))
    assert _split_text(text, 400) == [text]


@pytest.mark.asyncio
async def test_one_chunk_failure_does_not_break_others():
    """并行中某块抽取抛错，其余块结果照常入库。"""
    calls = []

    async def complete(system, user):
        idx = len(calls) + 1
        calls.append(user)
        if idx == 1:
            raise RuntimeError("boom")
        return f'[{{"type":"short","stem":"块{idx}题","answer":"a{idx}"}}]'
    imp = QuestionImporter(complete, QuestionStore(":memory:"), chunk_chars=400)
    res = await imp.import_text("u1", "f.txt", _BANK_TEXT)
    assert len(calls) > 1
    assert res["imported"] == len(calls) - 1        # 失败的那块丢弃，其余入库

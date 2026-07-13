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

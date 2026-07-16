import pytest

from app.exam_session import ExamSessionStore
from app.questions import QuestionStore
from app.wrong_answers import WrongAnswerStore
from app.tools.exam_tools import StartExamTool


def _q(**kw):
    d = {"type": "single", "stem": "1+1=?", "options": ["1", "2"], "answer": 1,
         "explanation": "", "source": "x"}
    d.update(kw)
    return d


async def test_start_from_bank_populates_session():
    qs = QuestionStore(":memory:"); es = ExamSessionStore(":memory:")
    qs.create("u1", _q())
    t = StartExamTool(es, "u1", "c1", question_store=qs)
    out = await t.run(t.Params(source="bank", count=5, mode="instant"))
    assert "已开始考试" in out and "第 1/1 题" in out
    sess = es.get_active("u1", "c1")
    assert sess is not None and sess["mode"] == "instant" and len(sess["questions"]) == 1


async def test_start_from_wrong_uses_snapshots():
    ws = WrongAnswerStore(":memory:"); es = ExamSessionStore(":memory:")
    snap = {"type": "truefalse", "stem": "地球是圆的", "options": None, "answer": True, "explanation": ""}
    ws.create("u1", "q1", "e", snap, False)
    t = StartExamTool(es, "u1", "c1", wrong_store=ws)
    out = await t.run(t.Params(source="wrong", count=3))
    assert "已开始考试" in out
    assert es.get_active("u1", "c1")["questions"][0]["stem"] == "地球是圆的"


async def test_start_adhoc_requires_questions():
    es = ExamSessionStore(":memory:")
    t = StartExamTool(es, "u1", "c1")
    out = await t.run(t.Params(source="adhoc", questions=[]))
    assert "无法开始考试" in out
    assert es.get_active("u1", "c1") is None


async def test_start_adhoc_validates_and_stores():
    es = ExamSessionStore(":memory:")
    t = StartExamTool(es, "u1", "c1")
    qlist = [{"type": "single", "stem": "选对的", "options": ["A", "B"], "answer": 0, "explanation": ""}]
    out = await t.run(t.Params(source="adhoc", questions=qlist, mode="graded"))
    assert "已开始考试" in out and "打分式" in out
    assert es.get_active("u1", "c1")["mode"] == "graded"


async def test_start_from_ids_exams_exactly_those_questions_in_order():
    """「刚才的 5 道题，考试」：题库里另有大量干扰题，也必须只考指定的那几道、按给定顺序。"""
    qs = QuestionStore(":memory:"); es = ExamSessionStore(":memory:")
    for i in range(20):
        qs.create("u1", _q(stem=f"干扰题{i}"))

    picked = [qs.create("u1", _q(stem=f"刚生成的题{i}")) for i in range(5)]
    t = StartExamTool(es, "u1", "c1", question_store=qs)
    out = await t.run(t.Params(source="ids", question_ids=list(reversed(picked))))
    assert "已开始考试：共 5 题" in out
    sess = es.get_active("u1", "c1")
    assert [q["stem"] for q in sess["questions"]] == [f"刚生成的题{i}" for i in reversed(range(5))]


async def test_start_from_ids_requires_ids():
    qs = QuestionStore(":memory:"); es = ExamSessionStore(":memory:")
    qs.create("u1", _q())
    t = StartExamTool(es, "u1", "c1", question_store=qs)
    out = await t.run(t.Params(source="ids", question_ids=[]))
    assert "无法开始考试" in out
    assert es.get_active("u1", "c1") is None


async def test_start_from_ids_all_missing_does_not_fall_back_to_random():
    """指定的题都找不到时必须报错，绝不能静默降级成全库随机抽。"""
    qs = QuestionStore(":memory:"); es = ExamSessionStore(":memory:")
    qs.create("u1", _q(stem="别的题"))
    t = StartExamTool(es, "u1", "c1", question_store=qs)
    out = await t.run(t.Params(source="ids", question_ids=["nope1", "nope2"]))
    assert "无法开始考试" in out
    assert es.get_active("u1", "c1") is None


async def test_start_from_ids_partial_hit_tells_model_to_disclose():
    qs = QuestionStore(":memory:"); es = ExamSessionStore(":memory:")
    kept = qs.create("u1", _q(stem="还在的题"))
    t = StartExamTool(es, "u1", "c1", question_store=qs)
    out = await t.run(t.Params(source="ids", question_ids=[kept, "已删除的id"]))
    assert "找不到" in out and "如实告知" in out
    assert len(es.get_active("u1", "c1")["questions"]) == 1


async def test_start_from_ids_does_not_leak_other_users_questions():
    qs = QuestionStore(":memory:"); es = ExamSessionStore(":memory:")
    mine = qs.create("u1", _q(stem="我的题"))
    theirs = qs.create("u2", _q(stem="别人的题"))
    t = StartExamTool(es, "u1", "c1", question_store=qs)
    out = await t.run(t.Params(source="ids", question_ids=[mine, theirs]))
    assert [q["stem"] for q in es.get_active("u1", "c1")["questions"]] == ["我的题"]
    assert "找不到" in out


async def test_start_empty_bank_does_not_create_session():
    qs = QuestionStore(":memory:"); es = ExamSessionStore(":memory:")
    t = StartExamTool(es, "u1", "c1", question_store=qs)
    out = await t.run(t.Params(source="bank"))
    assert "题库为空" in out
    assert es.get_active("u1", "c1") is None

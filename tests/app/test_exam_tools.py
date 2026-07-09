import json

from app.questions import QuestionStore
from app.tools.exam_tools import SampleQuestionsTool, SaveWrongAnswerTool
from app.wrong_answers import WrongAnswerStore


def _q(t="single"):
    return {"type": t, "stem": "题干", "options": ["A", "B"], "answer": 1, "explanation": "解析"}


async def test_sample_returns_only_own_questions():
    qs = QuestionStore(":memory:")
    qs.create("u1", _q()); qs.create("u1", _q())
    qs.create("u2", _q())                       # 他人题目不应被抽到
    tool = SampleQuestionsTool(qs, "u1")
    data = json.loads(await tool.run(tool.Params(count=10)))
    assert len(data) == 2 and all("answer" in q for q in data)


async def test_sample_empty_bank_message():
    tool = SampleQuestionsTool(QuestionStore(":memory:"), "u1")
    assert "题库为空" in await tool.run(tool.Params(count=5))


async def test_save_wrong_records_snapshot_and_survives_question_delete():
    qs = QuestionStore(":memory:")
    ws = WrongAnswerStore(":memory:")
    qid = qs.create("u1", _q())
    tool = SaveWrongAnswerTool(qs, ws, "u1")
    assert "已保存" in await tool.run(tool.Params(question_id=qid, user_answer=0))
    lst = ws.list("u1")
    assert len(lst) == 1 and lst[0]["snapshot"]["stem"] == "题干"
    qs.delete("u1", qid)                        # 删原题后错题快照仍在
    still = ws.list("u1")
    assert len(still) == 1 and still[0]["snapshot"]["stem"] == "题干"


async def test_save_wrong_respects_user_isolation():
    qs = QuestionStore(":memory:")
    ws = WrongAnswerStore(":memory:")
    qid = qs.create("u1", _q())
    tool = SaveWrongAnswerTool(qs, ws, "u2")    # u2 试图保存 u1 的题
    assert "未找到" in await tool.run(tool.Params(question_id=qid, user_answer=0))
    assert ws.list("u2") == []

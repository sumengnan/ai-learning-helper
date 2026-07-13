import json

from app.questions import QuestionStore
from app.tools.exam_tools import (
    AddQuestionsTool,
    DeleteQuestionsTool,
    DeleteWrongAnswersTool,
    GenerateQuestionsTool,
    ListQuestionsTool,
    SampleQuestionsTool,
    SampleWrongAnswersTool,
    SaveWrongAnswerTool,
)
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


async def test_save_wrong_adhoc_question_without_bank_id():
    # 即席出题（题目不在题库、无 question_id）：直接传题目内容也能存入错题集
    ws = WrongAnswerStore(":memory:")
    tool = SaveWrongAnswerTool(QuestionStore(":memory:"), ws, "u1")
    out = await tool.run(tool.Params(
        stem="1+1=?", type="single", options=["1", "2"], answer=1,
        explanation="等于二", user_answer=0))
    assert "已保存" in out
    lst = ws.list("u1")
    assert len(lst) == 1
    assert lst[0]["snapshot"]["stem"] == "1+1=?" and lst[0]["snapshot"]["answer"] == 1
    assert lst[0]["user_answer"] == 0


async def test_save_wrong_needs_id_or_content():
    # 既无有效 question_id、又没给题目内容 → 无法保存（不静默成功）
    qs = QuestionStore(":memory:")
    ws = WrongAnswerStore(":memory:")
    qid = qs.create("u1", _q())
    tool = SaveWrongAnswerTool(qs, ws, "u2")    # u2 引用 u1 的 id 且没给内容
    out = await tool.run(tool.Params(question_id=qid, user_answer=0))
    assert "无法保存" in out and ws.list("u2") == []


# ---- add_questions ----

async def test_add_questions_saves_valid_and_skips_invalid():
    qs = QuestionStore(":memory:")
    tool = AddQuestionsTool(qs, "u1")
    good = {"type": "single", "stem": "题", "options": ["A", "B"], "answer": 1}
    bad = {"type": "single", "stem": "题", "options": ["A", "B"], "answer": 9}  # 越界
    out = await tool.run(tool.Params(questions=[good, bad]))
    assert "1" in out                            # 入库 1 道
    saved = qs.list("u1")
    assert len(saved) == 1 and saved[0]["stem"] == "题"


async def test_add_questions_all_invalid():
    qs = QuestionStore(":memory:")
    tool = AddQuestionsTool(qs, "u1")
    out = await tool.run(tool.Params(questions=[{"type": "x", "stem": ""}]))
    assert qs.list("u1") == []
    assert "0" in out or "无" in out


async def test_add_questions_accepts_json_string_arg():
    """模型常把 questions 传成 JSON 字符串（可能带首尾换行）→ 应容错解析、正常入库。"""
    qs = QuestionStore(":memory:")
    tool = AddQuestionsTool(qs, "u1")
    raw = '\n[{"type": "single", "stem": "题", "options": ["A", "B"], "answer": 1}]\n'
    params = tool.Params.model_validate({"questions": raw})   # 模拟工具执行时的参数校验
    assert isinstance(params.questions, list)
    out = await tool.run(params)
    assert "1" in out and len(qs.list("u1")) == 1


async def test_delete_questions_accepts_json_string_ids():
    qs = QuestionStore(":memory:")
    qid = qs.create("u1", {"type": "single", "stem": "题", "options": ["A", "B"], "answer": 1})
    tool = DeleteQuestionsTool(qs, "u1")
    params = tool.Params.model_validate({"question_ids": json.dumps([qid])})
    assert params.question_ids == [qid]
    await tool.run(params)
    assert qs.list("u1") == []


async def test_sample_questions_types_accepts_json_string_and_none():
    """题型筛选 types 传 JSON 字符串→解析；省略(None)→原样通过。"""
    P = SampleQuestionsTool(QuestionStore(":memory:"), "u1").Params
    assert P.model_validate({"count": 5, "types": '["single","truefalse"]'}).types == \
        ["single", "truefalse"]
    assert P.model_validate({"count": 5}).types is None


# ---- list_questions ----

async def test_list_questions_returns_id_and_stem():
    qs = QuestionStore(":memory:")
    qid = qs.create("u1", _q())
    qs.create("u2", _q())                        # 他人题目不出现
    data = json.loads(await ListQuestionsTool(qs, "u1").run(ListQuestionsTool.Params()))
    assert len(data) == 1 and data[0]["id"] == qid and "stem" in data[0]


async def test_list_questions_empty():
    out = await ListQuestionsTool(QuestionStore(":memory:"), "u1").run(
        ListQuestionsTool.Params())
    assert "题库为空" in out


# ---- delete_questions ----

async def test_delete_questions_removes_and_isolates():
    qs = QuestionStore(":memory:")
    a = qs.create("u1", _q())
    b = qs.create("u2", _q())
    out = await DeleteQuestionsTool(qs, "u1").run(
        DeleteQuestionsTool.Params(question_ids=[a, b]))  # b 属于 u2，删不到
    assert "1" in out
    assert qs.list("u1") == [] and len(qs.list("u2")) == 1


# ---- sample_wrong_answers ----

async def test_sample_wrong_answers_returns_snapshot_with_answer():
    ws = WrongAnswerStore(":memory:")
    ws.create("u1", "q1", "chat", {"type": "single", "stem": "题", "options": ["A", "B"],
                                   "answer": 1, "explanation": "解析"}, 0)
    data = json.loads(await SampleWrongAnswersTool(ws, "u1").run(
        SampleWrongAnswersTool.Params(count=5)))
    assert len(data) == 1 and data[0]["snapshot"]["answer"] == 1 and "id" in data[0]


async def test_sample_wrong_answers_empty():
    out = await SampleWrongAnswersTool(WrongAnswerStore(":memory:"), "u1").run(
        SampleWrongAnswersTool.Params(count=5))
    assert "错题集为空" in out


# ---- delete_wrong_answers ----

async def test_delete_wrong_answers_removes():
    ws = WrongAnswerStore(":memory:")
    wid = ws.create("u1", "q1", "chat", _q(), 0)
    out = await DeleteWrongAnswersTool(ws, "u1").run(
        DeleteWrongAnswersTool.Params(wrong_answer_ids=[wid]))
    assert "1" in out and ws.list("u1") == []


# ---- generate_questions（stub quiz_service，不触真实 embedding）----

class _StubQuiz:
    def __init__(self, result=None, exc=None):
        self._result, self._exc = result, exc

    async def generate(self, user_id, topic, count, types):
        if self._exc:
            raise self._exc
        return self._result


async def test_generate_questions_success():
    tool = GenerateQuestionsTool(_StubQuiz(result=[_q(), _q()]), "u1")
    out = await tool.run(tool.Params(topic="光合作用", count=2))
    assert "2" in out


async def test_generate_questions_no_knowledge():
    from app.quiz_service import NoKnowledge
    tool = GenerateQuestionsTool(_StubQuiz(exc=NoKnowledge("光合作用")), "u1")
    out = await tool.run(tool.Params(topic="光合作用"))
    assert "知识库" in out


async def test_generate_questions_quiz_error():
    from app.quiz_service import QuizError
    tool = GenerateQuestionsTool(_StubQuiz(exc=QuizError("bad")), "u1")
    out = await tool.run(tool.Params(topic="x"))
    assert "失败" in out


async def test_add_questions_dedups_against_bank():
    qs = QuestionStore(":memory:")
    qs.create("u1", {"type": "single", "stem": "重复题", "options": ["A", "B"], "answer": 0})
    tool = AddQuestionsTool(qs, "u1")
    dup = {"type": "single", "stem": "重复题", "options": ["A", "B"], "answer": 0}
    fresh = {"type": "single", "stem": "新题", "options": ["A", "B"], "answer": 1}
    out = await tool.run(tool.Params(questions=[dup, fresh]))
    assert "1" in out and len(qs.list("u1")) == 2      # 只新增「新题」

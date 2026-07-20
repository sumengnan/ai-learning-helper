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


async def test_generate_questions_emits_id_marker():
    """真实 quiz_service.generate 会给每题带 id；工具须把 id 透出，
    否则模型接不住「就考刚才生成的那几道」。"""
    made = [dict(_q(), id="qa"), dict(_q(), id="qb")]
    tool = GenerateQuestionsTool(_StubQuiz(result=made), "u1")
    out = await tool.run(tool.Params(topic="光合作用", count=2))
    assert "〔题目ID:qa,qb〕" in out


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


# ---------- 题目 id 不外露：结果里要给出题干，模型才有名字可写 ----------

async def test_add_questions_result_lists_stems():
    """回归：工具只回 id 时，模型写学习计划就只能罗列一串哈希——对用户毫无意义。
    结果里必须带题干，模型才有「名字」可用。"""
    store = QuestionStore(":memory:")
    t = AddQuestionsTool(store, "u1")
    out = await t.run(t.Params(questions=[
        {"type": "single", "stem": "什么是自注意力机制？", "options": ["A", "B"], "answer": 0},
        {"type": "truefalse", "stem": "Transformer 摒弃了循环结构", "answer": True}]))
    assert "1. 什么是自注意力机制？" in out
    assert "2. Transformer 摒弃了循环结构" in out
    assert "不要写进给用户的回答" in out      # 最贴近的一次提醒，就在 id 前面
    assert "〔题目ID:" in out                  # 机读标记仍在（交付门清理 + start_exam 要用）


async def test_add_questions_stem_order_matches_id_order():
    """题干顺序必须与 id 顺序一致：start_exam(source=ids) 按传入顺序出题，
    错位会导致「就考刚才第 2 道」考出另一道。"""
    store = QuestionStore(":memory:")
    t = AddQuestionsTool(store, "u1")
    out = await t.run(t.Params(questions=[
        {"type": "single", "stem": "第一题", "options": ["A", "B"], "answer": 0},
        {"type": "single", "stem": "坏题无选项", "answer": 0},          # 非法，会被跳过
        {"type": "single", "stem": "第三题", "options": ["A", "B"], "answer": 1}]))
    ids = out.split("〔题目ID:")[1].rstrip("〕").split(",")
    assert len(ids) == 2                       # 非法题不入库
    lines = [ln for ln in out.splitlines() if ln[:2] in ("1.", "2.")]
    assert lines[0].endswith("第一题") and lines[1].endswith("第三题")


def test_exam_guide_forbids_exposing_question_ids():
    """EXAM_GUIDE 此前只教了怎么用 id（传 start_exam），从没说过别写给用户看。"""
    from app.api.chat import EXAM_GUIDE
    assert "题目 id 绝不出现在给用户的回答里" in EXAM_GUIDE
    assert "用题干" in EXAM_GUIDE


# ---------- 采样量 ----------

def test_sample_tools_default_to_ten():
    """默认 5 太少：题库/错题集抽两下就没了，复习也不见效。上限 50 不变。"""
    assert SampleQuestionsTool.Params().count == 10
    assert SampleWrongAnswersTool.Params().count == 10


async def test_sample_questions_honours_larger_count():
    store = QuestionStore(":memory:")
    for i in range(40):
        store.create("u1", {"type": "single", "stem": f"题{i}",
                            "options": ["A", "B"], "answer": 0})
    t = SampleQuestionsTool(store, "u1")
    out = json.loads(await t.run(t.Params(count=30)))
    assert len(out) == 30


async def test_sample_questions_clamps_to_fifty():
    store = QuestionStore(":memory:")
    for i in range(60):
        store.create("u1", {"type": "single", "stem": f"题{i}",
                            "options": ["A", "B"], "answer": 0})
    t = SampleQuestionsTool(store, "u1")
    out = json.loads(await t.run(t.Params(count=999)))
    assert len(out) == 50

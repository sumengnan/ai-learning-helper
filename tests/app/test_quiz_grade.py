import pytest
from app.quiz_service import QuizService


def _svc(complete=None):
    # grade 不用 memory/store；generate 才用。这里传 None。
    return QuizService(memory=None, question_store=None, complete=complete,
                       short_pass_score=60)


@pytest.mark.asyncio
async def test_grade_single():
    svc = _svc()
    q = {"type": "single", "answer": 1}
    assert (await svc.grade(q, 1))["correct"] is True
    assert (await svc.grade(q, 0))["correct"] is False


@pytest.mark.asyncio
async def test_grade_truefalse():
    svc = _svc()
    q = {"type": "truefalse", "answer": True}
    assert (await svc.grade(q, True))["correct"] is True
    assert (await svc.grade(q, False))["correct"] is False


@pytest.mark.asyncio
async def test_grade_multiple_set_equality():
    svc = _svc()
    q = {"type": "multiple", "answer": [0, 2]}
    assert (await svc.grade(q, [2, 0]))["correct"] is True      # 顺序无关
    assert (await svc.grade(q, [0]))["correct"] is False        # 缺一个
    assert (await svc.grade(q, [0, 1, 2]))["correct"] is False  # 多一个


@pytest.mark.asyncio
async def test_grade_short_uses_llm_and_threshold():
    async def fake(system, user):
        return '{"score": 80, "feedback": "答得不错"}'
    svc = _svc(complete=fake)
    q = {"type": "short", "answer": "光合作用把光能转化为化学能"}
    res = await svc.grade(q, "植物用光能合成有机物")
    assert res["correct"] is True and res["score"] == 80
    assert res["feedback"] == "答得不错"


@pytest.mark.asyncio
async def test_grade_short_below_threshold_is_wrong():
    async def fake(system, user):
        return '{"score": 30, "feedback": "偏离要点"}'
    svc = _svc(complete=fake)
    q = {"type": "short", "answer": "ref"}
    assert (await svc.grade(q, "乱答"))["correct"] is False

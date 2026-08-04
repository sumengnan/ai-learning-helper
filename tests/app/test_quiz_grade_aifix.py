import pytest
from app.quiz_service import QuizService


def _svc():
    return QuizService(memory=None, question_store=None, complete=None,
                       short_pass_score=60)


@pytest.mark.asyncio
async def test_grade_multiple_dedup_and_none_safe():
    """多选题只看选中了哪些选项（集合相等），与重复次数和顺序无关；
    answer 或作答缺失时不抛异常，判为错。"""
    svc = _svc()
    q = {"type": "multiple", "answer": [0, 1]}

    # 重复勾选同一选项不改变正确性
    assert (await svc.grade(q, [0, 0, 1]))["correct"] is True

    # 顺序无关
    assert (await svc.grade(q, [1, 0]))["correct"] is True

    # 作答缺失（None）不崩溃，判错
    assert (await svc.grade(q, None))["correct"] is False

    # answer 为 None 时不崩溃，判错
    q_none = {"type": "multiple", "answer": None}
    assert (await svc.grade(q_none, [0, 1]))["correct"] is False

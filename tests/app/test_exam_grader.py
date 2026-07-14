import pytest

from app.exam_grader import (
    END_INTENT_RE, grade_objective, grade_short, parse_choice)


def _single():
    return {"type": "single", "stem": "光合作用在哪?", "options": ["线粒体", "叶绿体"], "answer": 1}


def _multi():
    return {"type": "multiple", "stem": "选出偶数", "options": ["1", "2", "3", "4"], "answer": [1, 3]}


def _tf():
    return {"type": "truefalse", "stem": "地球是圆的", "options": None, "answer": True}


# ---- 解析：单选 ----
@pytest.mark.parametrize("text,expect", [
    ("B", 1), ("b", 1), ("选B", 1), ("2", 1), ("叶绿体", 1),
    ("我觉得是 B 叶绿体", 1),   # 字母与文本都指向 1，不冲突
    ("A", 0),
])
def test_parse_single_choice(text, expect):
    assert parse_choice(text, _single()) == expect


def test_parse_single_ambiguous_or_empty_is_none():
    assert parse_choice("AB", _single()) is None       # 两个不同下标 → 歧义
    assert parse_choice("不知道", _single()) is None    # 没命中


# ---- 解析：多选 ----
@pytest.mark.parametrize("text,expect", [
    ("BD", [1, 3]), ("2、4", [1, 3]), ("B和D", [1, 3]), ("选 2 4", [1, 3]),
])
def test_parse_multiple_choice(text, expect):
    assert parse_choice(text, _multi()) == expect


# ---- 解析：判断 ----
@pytest.mark.parametrize("text,expect", [
    ("对", True), ("正确", True), ("是的", True), ("√", True),
    ("错", False), ("错误", False), ("不对", False), ("×", False), ("否", False),
])
def test_parse_truefalse(text, expect):
    assert parse_choice(text, _tf()) is expect


# ---- 判分：确定性 ----
def test_grade_single():
    assert grade_objective(_single(), 1) is True
    assert grade_objective(_single(), 0) is False


def test_grade_multiple_exact_set():
    assert grade_objective(_multi(), [1, 3]) is True
    assert grade_objective(_multi(), [1]) is False        # 少选
    assert grade_objective(_multi(), [1, 2, 3]) is False   # 多选


def test_grade_truefalse():
    assert grade_objective(_tf(), True) is True
    assert grade_objective(_tf(), False) is False


# ---- 简答：judge ----
async def test_grade_short_correct():
    async def judge(system, user):
        return '{"correct": true, "feedback": "要点齐全"}'
    ok, fb = await grade_short(judge, {"type": "short", "stem": "什么是光合作用", "answer": "光能转化学能"}, "把光能变成化学能")
    assert ok is True and "要点" in fb


async def test_grade_short_wrong():
    async def judge(system, user):
        return '{"correct": false, "feedback": "答非所问"}'
    ok, _ = await grade_short(judge, {"type": "short", "stem": "x", "answer": "y"}, "乱答")
    assert ok is False


async def test_grade_short_judge_failure_is_conservative():
    async def boom(system, user):
        raise RuntimeError("judge down")
    ok, _ = await grade_short(boom, {"type": "short", "stem": "x", "answer": "y"}, "z")
    assert ok is True                                      # 保守：不算错，不误存


# ---- 结束意图 ----
@pytest.mark.parametrize("text", ["结束", "结束考试", "交卷", "不考了", "退出考试"])
def test_end_intent(text):
    assert END_INTENT_RE.search(text)


def test_normal_answer_not_end_intent():
    assert not END_INTENT_RE.search("我选B")

"""考试判分中间件：证明「答错→存错题集」由服务端确定性执行，全程无模型参与。"""
import pytest

from app.exam_flow import grade_exam_turn
from app.exam_session import ExamSessionStore
from app.wrong_answers import WrongAnswerStore


async def _never_judge(system, user):
    raise AssertionError("客观题判分不应调用 judge 模型")


def _single(ans=1):
    return {"type": "single", "stem": "光合作用在哪?", "options": ["线粒体", "叶绿体"],
            "answer": ans, "explanation": "叶绿体"}


def _setup(mode="instant", questions=None):
    es = ExamSessionStore(":memory:")
    ws = WrongAnswerStore(":memory:")
    es.start("u1", "c1", questions or [_single(), _single(ans=0)], mode)
    return es, ws


async def test_wrong_objective_saved_deterministically_no_model():
    es, ws = _setup()
    note, active = await grade_exam_turn(es, ws, _never_judge,
                                         user_id="u1", conv_id="c1", message="A")
    assert len(ws.list("u1")) == 1                       # 答错 → 服务端确定性入库
    assert ws.list("u1")[0]["snapshot"]["stem"] == "光合作用在哪?"
    assert ws.list("u1")[0]["user_answer"] == 0          # 存的是解析后的下标
    assert "答错" in note and "叶绿体" in note            # 注入提示含判定+正确答案
    # 答错 → 注入提示要求模型明确告诉用户已入错题集
    assert "错题集" in note and ("明确告诉用户" in note or "加入你的错题集" in note)
    assert active is True                                 # 还有下一题


async def test_correct_objective_not_saved():
    es, ws = _setup()
    note, _ = await grade_exam_turn(es, ws, _never_judge,
                                    user_id="u1", conv_id="c1", message="B")
    assert ws.list("u1") == [] and "答对" in note


async def test_saving_is_unconditional_no_opt_out():
    """答错即存，没有任何开关/参数能关掉（旧的 save_wrong 开关已移除）。"""
    import inspect
    assert "save_wrong" not in inspect.signature(grade_exam_turn).parameters
    es, ws = _setup()
    await grade_exam_turn(es, ws, _never_judge,
                          user_id="u1", conv_id="c1", message="A")
    assert len(ws.list("u1")) == 1                        # 答错 → 必入库
    assert es.get_active("u1", "c1")["cursor"] == 1       # 且正常推进


async def test_unrecognized_answer_does_not_save_or_advance():
    es, ws = _setup()
    note, active = await grade_exam_turn(es, ws, _never_judge,
                                         user_id="u1", conv_id="c1", message="不知道")
    assert ws.list("u1") == [] and "未能识别" in note
    assert es.get_active("u1", "c1")["cursor"] == 0 and active is True   # 不推进、仍在本题


async def test_last_question_finishes_and_ends_session():
    es, ws = _setup(questions=[_single()])                # 只有一题
    note, active = await grade_exam_turn(es, ws, _never_judge,
                                         user_id="u1", conv_id="c1", message="A")
    assert active is False and es.get_active("u1", "c1") is None   # 已结束
    assert "结束" in note or "最后一题" in note


async def test_graded_mode_hides_verdict_then_summarizes():
    q1 = {"type": "single", "stem": "Q1", "options": ["a1", "b1"], "answer": 1, "explanation": ""}
    q2 = {"type": "single", "stem": "Q2", "options": ["a2", "b2"], "answer": 0, "explanation": ""}
    es, ws = _setup(mode="graded", questions=[q1, q2])
    n1, _ = await grade_exam_turn(es, ws, _never_judge,
                                  user_id="u1", conv_id="c1", message="A")  # Q1错(选A,答案B)
    assert "透露" in n1 and "b1" not in n1                        # 打分式不揭晓 Q1 正确答案(b1)
    n2, active = await grade_exam_turn(es, ws, _never_judge,
                                       user_id="u1", conv_id="c1", message="A")  # Q2对(选A=0)
    assert active is False and "得分" in n2 and "1/2" in n2       # 结束公布成绩
    assert len(ws.list("u1")) == 1                                # 仅 Q1 错入库


async def test_end_intent_ends_without_grading():
    es, ws = _setup()
    note, active = await grade_exam_turn(es, ws, _never_judge,
                                         user_id="u1", conv_id="c1", message="结束考试")
    assert active is False and es.get_active("u1", "c1") is None
    assert ws.list("u1") == [] and "结束" in note


async def test_restart_intent_ends_stale_exam_without_grading():
    """考试进行中收到「重开/换考」意图：结束旧场、放行（不判分/不存/不推进），让模型重新开考。

    覆盖 Bug：残留的旧考试会把新的「考考我」当作对当前题的作答吞掉，使新考试永远起不来。"""
    es, ws = _setup()                                    # 有一场 active 客观题考试
    for msg in ("考考我", "换一套题", "重新考", "再来一套"):
        es, ws = _setup()
        note, active = await grade_exam_turn(es, ws, _never_judge,
                                             user_id="u1", conv_id="c1", message=msg)
        assert note == "" and active is False            # 放行：无注入提示、考试不再活跃
        assert es.get_active("u1", "c1") is None          # 旧场已结束
        assert ws.list("u1") == []                        # 未被当作作答存错题


async def test_short_question_uses_judge_and_saves_on_wrong():
    async def judge(system, user):
        return '{"correct": false, "feedback": "答非所问"}'
    es = ExamSessionStore(":memory:"); ws = WrongAnswerStore(":memory:")
    es.start("u1", "c1", [{"type": "short", "stem": "什么是光合作用", "options": None,
                           "answer": "光能转化学能", "explanation": ""}], "instant")
    note, _ = await grade_exam_turn(es, ws, judge,
                                    user_id="u1", conv_id="c1", message="乱答")
    assert len(ws.list("u1")) == 1 and "答错" in note


async def test_no_active_exam_is_noop():
    es = ExamSessionStore(":memory:"); ws = WrongAnswerStore(":memory:")
    note, active = await grade_exam_turn(es, ws, _never_judge,
                                         user_id="u1", conv_id="c1", message="随便")
    assert note == "" and active is False

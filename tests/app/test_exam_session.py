from app.exam_session import ExamSessionStore


def _qs():
    return [
        {"type": "single", "stem": "1+1=?", "options": ["1", "2"], "answer": 1, "explanation": ""},
        {"type": "truefalse", "stem": "地球是圆的", "options": None, "answer": True, "explanation": ""},
    ]


def test_start_get_current():
    s = ExamSessionStore(":memory:")
    s.start("u1", "c1", _qs(), "instant")
    sess = s.get_active("u1", "c1")
    assert sess is not None and sess["mode"] == "instant" and sess["cursor"] == 0
    assert ExamSessionStore.current(sess)["stem"] == "1+1=?"


def test_record_advances_and_tracks_results():
    s = ExamSessionStore(":memory:")
    s.start("u1", "c1", _qs(), "instant")
    s.record("u1", "c1", 0, False)                 # 第1题答错
    sess = s.get_active("u1", "c1")
    assert sess["cursor"] == 1
    assert ExamSessionStore.current(sess)["type"] == "truefalse"
    assert sess["results"] == [{"user_answer": 0, "is_correct": False}]
    s.record("u1", "c1", True, True)               # 第2题答对
    sess = s.get_active("u1", "c1")
    assert sess["cursor"] == 2 and ExamSessionStore.is_finished(sess)
    assert ExamSessionStore.current(sess) is None


def test_end_marks_inactive():
    s = ExamSessionStore(":memory:")
    s.start("u1", "c1", _qs(), "instant")
    s.end("u1", "c1")
    assert s.get_active("u1", "c1") is None


def test_start_overwrites_previous_active():
    s = ExamSessionStore(":memory:")
    s.start("u1", "c1", _qs(), "instant")
    s.record("u1", "c1", 0, False)                 # 推进到第2题
    s.start("u1", "c1", _qs()[:1], "graded")       # 重开：覆盖、游标归零
    sess = s.get_active("u1", "c1")
    assert sess["mode"] == "graded" and sess["cursor"] == 0 and sess["results"] == []
    assert len(sess["questions"]) == 1


def test_user_and_conversation_isolation():
    s = ExamSessionStore(":memory:")
    s.start("u1", "c1", _qs(), "instant")
    assert s.get_active("u2", "c1") is None         # 他人取不到
    assert s.get_active("u1", "c2") is None         # 别的会话取不到

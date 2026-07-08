from app.exams import ExamStore


def test_create_and_list():
    s = ExamStore(":memory:")
    detail = [{"question_id": "q1", "correct": True}]
    eid = s.create(total=1, correct=1, score=100.0, detail=detail)
    rows = s.list()
    assert len(rows) == 1
    assert rows[0]["id"] == eid
    assert rows[0]["total"] == 1 and rows[0]["correct"] == 1
    assert rows[0]["score"] == 100.0
    assert rows[0]["detail"] == detail            # JSON 往返


def test_list_newest_first():
    s = ExamStore(":memory:")
    first = s.create(1, 0, 0.0, [])
    second = s.create(1, 1, 100.0, [])
    assert [r["id"] for r in s.list()] == [second, first]   # 倒序

from app.wrong_answers import WrongAnswerStore


def _snap():
    return {"type": "single", "stem": "1+1=?", "options": ["1", "2"],
            "answer": 1, "explanation": "等于二"}


def test_create_list_snapshot_roundtrip():
    s = WrongAnswerStore(":memory:")
    wid = s.create("u1", question_id="q1", exam_id="e1", snapshot=_snap(), user_answer=0)
    rows = s.list("u1")
    assert len(rows) == 1
    assert rows[0]["id"] == wid
    assert rows[0]["question_id"] == "q1" and rows[0]["exam_id"] == "e1"
    assert rows[0]["snapshot"] == _snap()          # 快照 JSON 往返
    assert rows[0]["user_answer"] == 0


def test_delete_many():
    s = WrongAnswerStore(":memory:")
    ids = [s.create("u1", "q", "e", _snap(), 0) for _ in range(3)]
    s.delete_many("u1", ids[:2])
    assert [r["id"] for r in s.list("u1")] == [ids[2]]


def test_delete_one():
    s = WrongAnswerStore(":memory:")
    a = s.create("u1", "q", "e", _snap(), 0)
    s.delete("u1", a)
    assert s.list("u1") == []

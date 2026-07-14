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


def test_sample_returns_snapshots_within_count():
    s = WrongAnswerStore(":memory:")
    for _ in range(3):
        s.create("u1", "q", "e", _snap(), 0)
    got = s.sample("u1", 2)
    assert len(got) == 2 and all(r["snapshot"] == _snap() for r in got)


def test_sample_respects_user_isolation():
    s = WrongAnswerStore(":memory:")
    s.create("u1", "q", "e", _snap(), 0)
    s.create("u2", "q", "e", _snap(), 0)          # 他人错题不应被抽到
    got = s.sample("u1", 10)
    assert len(got) == 1


def test_sample_empty_returns_empty_list():
    assert WrongAnswerStore(":memory:").sample("u1", 5) == []


def test_count_by_question_and_user_isolation():
    s = WrongAnswerStore(":memory:")
    s.create("u1", "qA", "e", _snap(), 0)
    s.create("u1", "qA", "e", _snap(), 0)          # 同题两条
    s.create("u1", "qB", "e", _snap(), 0)
    s.create("u2", "qA", "e", _snap(), 0)          # 他人错题不计
    assert s.count_by_question("u1", ["qA"]) == 2
    assert s.count_by_question("u1", ["qA", "qB"]) == 3
    assert s.count_by_question("u1", ["qZ"]) == 0
    assert s.count_by_question("u1", []) == 0


def test_delete_by_question_removes_only_matching():
    s = WrongAnswerStore(":memory:")
    s.create("u1", "qA", "e", _snap(), 0)
    s.create("u1", "qA", "e", _snap(), 0)
    s.create("u1", "qB", "e", _snap(), 0)
    s.create("u2", "qA", "e", _snap(), 0)          # 他人错题不受影响
    removed = s.delete_by_question("u1", ["qA"])
    assert removed == 2
    assert [r["question_id"] for r in s.list("u1")] == ["qB"]
    assert len(s.list("u2")) == 1
    assert s.delete_by_question("u1", []) == 0

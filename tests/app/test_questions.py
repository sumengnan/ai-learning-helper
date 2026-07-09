from app.questions import QuestionStore


def _q(type="single", stem="1+1=?", options=["1", "2", "3"], answer=1):
    return {"type": type, "stem": stem, "options": options, "answer": answer,
            "explanation": "因为等于二", "source": "算术"}


def test_create_and_get_roundtrip():
    s = QuestionStore(":memory:")
    qid = s.create("u1", _q())
    got = s.get("u1", qid)
    assert got["id"] == qid
    assert got["type"] == "single"
    assert got["options"] == ["1", "2", "3"]   # JSON 往返
    assert got["answer"] == 1


def test_answer_json_types_roundtrip():
    s = QuestionStore(":memory:")
    multi = s.get("u1", s.create("u1", _q(type="multiple", answer=[0, 2])))
    tf = s.get("u1", s.create("u1", _q(type="truefalse", options=None, answer=True)))
    short = s.get("u1", s.create("u1", _q(type="short", options=None, answer="光合作用")))
    assert multi["answer"] == [0, 2]
    assert tf["answer"] is True and tf["options"] is None
    assert short["answer"] == "光合作用"


def test_list_and_delete():
    s = QuestionStore(":memory:")
    a = s.create("u1", _q()); b = s.create("u1", _q(stem="2+2=?"))
    assert {q["id"] for q in s.list("u1")} == {a, b}
    s.delete("u1", a)
    assert {q["id"] for q in s.list("u1")} == {b}


def test_delete_many():
    s = QuestionStore(":memory:")
    ids = [s.create("u1", _q(stem=f"q{i}")) for i in range(3)]
    s.delete_many("u1", ids[:2])
    assert [q["id"] for q in s.list("u1")] == [ids[2]]


def test_sample_count_and_type_filter():
    s = QuestionStore(":memory:")
    for _ in range(5): s.create("u1", _q(type="single"))
    for _ in range(5): s.create("u1", _q(type="truefalse", options=None, answer=True))
    assert len(s.sample("u1", 3, None)) == 3
    only_tf = s.sample("u1", 10, ["truefalse"])
    assert len(only_tf) == 5 and all(q["type"] == "truefalse" for q in only_tf)

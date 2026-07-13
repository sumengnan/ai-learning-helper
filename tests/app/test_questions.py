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


def test_create_deduped_skips_same_type_and_stem():
    s = QuestionStore(":memory:")
    a = s.create_deduped("u1", _q(stem="1+1=?"))
    dup = s.create_deduped("u1", _q(stem="  1+1=?  "))   # 首尾空白等价
    assert a is not None and dup is None
    assert len(s.list("u1")) == 1


def test_create_deduped_allows_diff_type_or_user():
    s = QuestionStore(":memory:")
    s.create_deduped("u1", _q(type="single", stem="同题干"))
    diff_type = s.create_deduped("u1", _q(type="short", options=None,
                                          answer="x", stem="同题干"))
    other_user = s.create_deduped("u2", _q(type="single", stem="同题干"))
    assert diff_type is not None and other_user is not None
    assert len(s.list("u1")) == 2 and len(s.list("u2")) == 1


def test_list_filter_and_pagination():
    s = QuestionStore(":memory:")
    for i in range(3):
        s.create("u1", _q(type="single", stem=f"单选{i}", options=["1", "2"], answer=1))
    s.create("u1", _q(type="truefalse", stem="判断题", options=None, answer=True))
    s.create("u1", _q(type="single", stem="含关键词KW", options=["1", "2"], answer=0))
    assert s.count("u1") == 5
    assert s.count("u1", type="single") == 4
    assert {q["stem"] for q in s.list("u1", type="truefalse")} == {"判断题"}
    assert s.count("u1", q="KW") == 1 and s.list("u1", q="KW")[0]["stem"] == "含关键词KW"
    page1 = s.list("u1", limit=2, offset=0)
    page2 = s.list("u1", limit=2, offset=2)
    assert len(page1) == 2 and len(page2) == 2
    assert {x["id"] for x in page1}.isdisjoint({x["id"] for x in page2})   # 不重叠


def test_sources_distinct_nonempty_isolated():
    s = QuestionStore(":memory:")
    s.create("u1", _q(stem="a")); s.create("u1", _q(stem="b"))          # source="算术"
    s.create("u1", {"type": "single", "stem": "c", "options": ["1", "2"],
                    "answer": 0, "source": ""})                          # 空 source 排除
    s.create("u2", {"type": "single", "stem": "d", "options": ["1", "2"],
                    "answer": 0, "source": "他人"})                      # 他用户不出现
    assert s.sources("u1") == ["算术"]

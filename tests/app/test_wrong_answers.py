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
    # 题干各异，避免同题去重把三条并成一条
    ids = [s.create("u1", "q", "e", _snap_of("single", f"题{i}"), 0) for i in range(3)]
    s.delete_many("u1", ids[:2])
    assert [r["id"] for r in s.list("u1")] == [ids[2]]


def test_delete_one():
    s = WrongAnswerStore(":memory:")
    a = s.create("u1", "q", "e", _snap(), 0)
    s.delete("u1", a)
    assert s.list("u1") == []


def _snap_of(type, stem):
    return {"type": type, "stem": stem, "options": ["1", "2"],
            "answer": 1, "explanation": ""}


def test_list_filters_by_type_from_snapshot():
    s = WrongAnswerStore(":memory:")
    s.create("u1", "q", "e", _snap_of("single", "单选题"), 0)
    s.create("u1", "q", "e", _snap_of("truefalse", "判断题"), 0)
    got = s.list("u1", type="truefalse")
    assert [r["snapshot"]["stem"] for r in got] == ["判断题"]
    assert s.count("u1", type="truefalse") == 1


def test_list_filters_by_stem_keyword():
    s = WrongAnswerStore(":memory:")
    s.create("u1", "q", "e", _snap_of("single", "光合作用在哪"), 0)
    s.create("u1", "q", "e", _snap_of("single", "细胞呼吸在哪"), 0)
    got = s.list("u1", q="光合")
    assert [r["snapshot"]["stem"] for r in got] == ["光合作用在哪"]
    assert s.count("u1", q="光合") == 1
    assert s.count("u1", q="在哪") == 2          # 子串匹配


def test_list_combines_type_and_keyword():
    s = WrongAnswerStore(":memory:")
    s.create("u1", "q", "e", _snap_of("single", "光合作用"), 0)
    s.create("u1", "q", "e", _snap_of("truefalse", "光合作用"), 0)
    assert s.count("u1", type="single", q="光合") == 1


def test_list_paginates_newest_first():
    s = WrongAnswerStore(":memory:")
    for i in range(5):
        s.create("u1", "q", "e", _snap_of("single", f"题{i}"), 0)
    page1 = s.list("u1", limit=2, offset=0)
    page2 = s.list("u1", limit=2, offset=2)
    assert [r["snapshot"]["stem"] for r in page1] == ["题4", "题3"]   # seq 倒序
    assert [r["snapshot"]["stem"] for r in page2] == ["题2", "题1"]
    assert s.count("u1") == 5


def test_list_without_filters_returns_all():
    """无参调用保持旧行为（delete_wrong_answers 工具依赖）。"""
    s = WrongAnswerStore(":memory:")
    for i in range(3):
        s.create("u1", "q", "e", _snap_of("single", f"题{i}"), 0)
    assert len(s.list("u1")) == 3


def test_count_respects_user_isolation():
    s = WrongAnswerStore(":memory:")
    s.create("u1", "q", "e", _snap_of("single", "光合作用"), 0)
    s.create("u2", "q", "e", _snap_of("single", "光合作用"), 0)
    assert s.count("u1", q="光合") == 1


def test_sample_returns_snapshots_within_count():
    s = WrongAnswerStore(":memory:")
    for i in range(3):
        s.create("u1", "q", "e", _snap_of("single", f"题{i}"), 0)   # 题干各异，避免去重
    got = s.sample("u1", 2)
    assert len(got) == 2


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
    # 同题去重后每道题至多一条，故按题给不同题干；count_by_question 按 question_id 计
    s.create("u1", "qA", "e", _snap_of("single", "题A"), 0)
    s.create("u1", "qB", "e", _snap_of("single", "题B"), 0)
    s.create("u2", "qA", "e", _snap_of("single", "题A"), 0)   # 他人错题不计
    assert s.count_by_question("u1", ["qA"]) == 1
    assert s.count_by_question("u1", ["qA", "qB"]) == 2
    assert s.count_by_question("u1", ["qZ"]) == 0
    assert s.count_by_question("u1", []) == 0


def test_delete_by_question_removes_only_matching():
    s = WrongAnswerStore(":memory:")
    s.create("u1", "qA", "e", _snap_of("single", "题A"), 0)
    s.create("u1", "qB", "e", _snap_of("single", "题B"), 0)
    s.create("u2", "qA", "e", _snap_of("single", "题A"), 0)   # 他人错题不受影响
    removed = s.delete_by_question("u1", ["qA"])
    assert removed == 1
    assert [r["question_id"] for r in s.list("u1")] == ["qB"]
    assert len(s.list("u2")) == 1
    assert s.delete_by_question("u1", []) == 0


def test_create_dedups_same_question_replacing_data():
    """同一道题（题型+题干相同）再次入错题集：不新增，用新数据整条替换旧的。"""
    s = WrongAnswerStore(":memory:")
    first = s.create("u1", "", "chat", _snap_of("single", "光合作用在哪"), 0)
    second = s.create("u1", "qX", "exam", _snap_of("single", "光合作用在哪"), 1)
    assert second == first                        # 同一条 id：替换而非新增
    rows = s.list("u1")
    assert len(rows) == 1                          # 未新增
    assert rows[0]["user_answer"] == 1             # 新作答
    assert rows[0]["question_id"] == "qX" and rows[0]["exam_id"] == "exam"   # 新来源


def test_create_dedup_trims_stem_and_is_per_user():
    """判重对题干去首尾空白（与题库口径一致），且按用户隔离。"""
    s = WrongAnswerStore(":memory:")
    a = s.create("u1", "", "chat", _snap_of("single", "同一题"), 0)
    b = s.create("u1", "", "chat", _snap_of("single", "  同一题  "), 1)
    assert b == a and len(s.list("u1")) == 1       # 去空白后判为同题
    s.create("u2", "", "chat", _snap_of("single", "同一题"), 0)
    assert len(s.list("u2")) == 1                   # 他人同题各自保留


def test_create_distinct_questions_not_deduped():
    """题干不同或题型不同 → 不判重，各自入库。"""
    s = WrongAnswerStore(":memory:")
    s.create("u1", "", "chat", _snap_of("single", "题一"), 0)
    s.create("u1", "", "chat", _snap_of("single", "题二"), 0)       # 题干不同
    s.create("u1", "", "chat", _snap_of("truefalse", "题一"), 0)   # 题型不同
    assert len(s.list("u1")) == 3


def test_create_dedup_replace_bumps_to_top():
    """替换后刷新 seq，该题回到列表顶部（最近错的在前）。"""
    s = WrongAnswerStore(":memory:")
    s.create("u1", "", "chat", _snap_of("single", "老题"), 0)
    s.create("u1", "", "chat", _snap_of("single", "新题"), 0)      # 老题在下
    s.create("u1", "", "chat", _snap_of("single", "老题"), 1)      # 重做老题 → 回到顶部
    assert [r["snapshot"]["stem"] for r in s.list("u1")] == ["老题", "新题"]

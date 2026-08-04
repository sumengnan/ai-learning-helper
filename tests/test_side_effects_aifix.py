def test_drop_purged_marks_strips_question_markers():
    """题目被清理后，〔题目ID:...〕机读标记也要从落库步骤里剥掉。

    题目标记是逗号分组的（〔题目ID:q1,q2,q3〕），漏剥会让模型拿已删的 id 调 start_exam。
    """
    from app.api.chat import _drop_purged_marks

    steps = [
        {"tool": "save_download", "result": "已保存 note.md〔下载ID:d1〕"},
        {"tool": "generate_questions", "result": "已生成 3 道题\n〔题目ID:q1,q2,q3〕"},
    ]
    done = {"download": ["d1"], "knowledge": [], "questions": ["q1", "q2", "q3"]}

    _drop_purged_marks(steps, done)

    assert "〔下载ID:d1〕" not in steps[0]["result"]
    assert "已作废删除" in steps[0]["result"]
    # 题目全删了，逗号分组的标记也要一并剥掉，不能把已删的 id 留给模型
    assert "〔题目ID:q1,q2,q3〕" not in steps[1]["result"]
    assert "已作废删除" in steps[1]["result"]

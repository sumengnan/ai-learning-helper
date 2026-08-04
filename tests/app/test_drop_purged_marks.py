from app.api.chat import _drop_purged_marks


def test_drop_purged_marks_strips_question_mark():
    """产物被清理后，指向它的〔题目ID:...〕标记也该从落库步骤里剥掉。

    题目标记是逗号分组的（〔题目ID:q1,q2,q3〕），且该批次里有 id 被删时
    整条标记都要剥——否则模型会拿已删的 id 调 start_exam，部分失效时考试
    会静默少几道题。
    """
    steps = [
        {"tool": "save_download", "result": "已保存 note.md〔下载ID:d1〕"},
        {"tool": "generate_questions", "result": "已生成 3 道题\n〔题目ID:q1,q2,q3〕"},
    ]
    done = {"download": ["d1"], "knowledge": [], "questions": ["q1", "q2", "q3"]}
    _drop_purged_marks(steps, done)
    assert steps[0]["result"] == "已保存 note.md\n（该版本未通过校验，此产物已作废删除）"
    assert steps[1]["result"] == "已生成 3 道题\n（该版本未通过校验，此产物已作废删除）"

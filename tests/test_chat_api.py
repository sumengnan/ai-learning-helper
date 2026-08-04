from app.api.chat import _drop_purged_marks

def test_drop_purged_marks_with_questions():
    steps = [
        {"tool": "save_download", "result": "已保存 note.md〔下载ID:d1〕"},
        {"tool": "generate_questions", "result": "已生成 3 道题\n〔题目ID:q1,q2,q3〕"},
    ]
    done = {"download": ["d1"], "knowledge": [], "questions": ["q1", "q2", "q3"]}

    _drop_purged_marks(steps, done)

    assert steps[0]["result"] == "已保存 note.md\n（该版本未通过校验，此产物已作废删除）"
    assert steps[1]["result"] == "已生成 3 道题"

import pytest

from app.api.chat import _drop_purged_marks


def test_drop_purged_marks_strips_question_ids():
    """已删题目的〔题目ID:...〕标记应与下载/知识标记一样被剥掉。"""
    steps = [
        {"tool": "save_download", "result": "已保存 note.md〔下载ID:d1〕"},
        {"tool": "generate_questions", "result": "已生成 3 道题\n〔题目ID:q1,q2,q3〕"},
    ]
    done = {"download": ["d1"], "knowledge": [], "questions": ["q1", "q2", "q3"]}

    _drop_purged_marks(steps, done)

    # 下载标记已剥（当前已正确实现）
    assert "〔下载ID:d1〕" not in steps[0]["result"]
    # 题目标记也应剥掉 —— 当前漏了 fx["questions"]，这条会失败
    assert "〔题目ID:q1,q2,q3〕" not in steps[1]["result"]

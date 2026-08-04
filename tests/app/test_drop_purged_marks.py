"""_drop_purged_marks 对题目标记的清理。"""
import pytest

from app.api.chat import _drop_purged_marks


def test_drop_purged_marks_strips_question_markers():
    """校验不过删题后，〔题目ID:...〕标记也应从步骤结果里剥掉。"""
    steps = [
        {"tool": "save_download", "result": "已保存 note.md〔下载ID:d1〕"},
        {"tool": "generate_questions", "result": "已生成 3 道题\n〔题目ID:q1,q2,q3〕"},
    ]
    done = {"download": ["d1"], "knowledge": [], "questions": ["q1", "q2", "q3"]}

    _drop_purged_marks(steps, done)

    # 下载标记应被剥掉
    assert "〔下载ID:d1〕" not in steps[0]["result"]
    # 题目标记也应被剥掉——当前 bug：它还在
    assert "〔题目ID:q1,q2,q3〕" not in steps[1]["result"]

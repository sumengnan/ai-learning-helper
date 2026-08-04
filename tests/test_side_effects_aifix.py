import unittest
from app.api.chat import _drop_purged_marks

class TestDropPurgedMarks(unittest.TestCase):
    def test_questions_not_dropped(self):
        steps = [
            {"tool": "save_download", "result": "已保存 note.md〔下载ID:d1〕"},
            {"tool": "generate_questions", "result": "已生成 3 道题\n〔题目ID:q1,q2,q3〕"},
        ]
        done = {"download": ["d1"], "knowledge": [], "questions": ["q1", "q2", "q3"]}

        _drop_purged_marks(steps, done)

        self.assertNotIn("〔题目ID:q1,q2,q3〕", steps[1]["result"])
        self.assertIn("（该版本未通过校验，此产物已作废删除）", steps[1]["result"])

# app/tools/exam_tools.py
from __future__ import annotations

import json

from pydantic import BaseModel

from harness.tools.base import Tool


class SampleQuestionsTool(Tool):
    name = "sample_questions"
    description = (
        "从当前用户的题库中随机抽取题目用于模拟考试。返回的题目含答案与解析，"
        "仅供你出题与判分：在『打分式』考试中，作答完成前不要向用户透露答案。")

    class Params(BaseModel):
        count: int = 5
        types: list[str] | None = None

    def __init__(self, question_store, user_id: str) -> None:
        self._store = question_store
        self._uid = user_id

    async def run(self, params: "SampleQuestionsTool.Params") -> str:
        count = max(1, min(params.count, 50))
        qs = self._store.sample(self._uid, count, params.types)
        if not qs:
            return "题库为空。请提醒用户先到「题库」页面生成题目，或改用其他方式练习。"
        return json.dumps(qs, ensure_ascii=False)


class SaveWrongAnswerTool(Tool):
    name = "save_wrong_answer"
    description = (
        "把用户答错的题目保存到「错题集」。当用户某题作答错误时调用；"
        "传入该题的 question_id 与用户的作答 user_answer。")

    class Params(BaseModel):
        question_id: str
        user_answer: object = None

    def __init__(self, question_store, wrong_store, user_id: str) -> None:
        self._qs = question_store
        self._ws = wrong_store
        self._uid = user_id

    async def run(self, params: "SaveWrongAnswerTool.Params") -> str:
        q = self._qs.get(self._uid, params.question_id)
        if q is None:
            return "未找到该题目，无法保存到错题集。"
        snapshot = {"type": q["type"], "stem": q["stem"], "options": q["options"],
                    "answer": q["answer"], "explanation": q["explanation"]}
        self._ws.create(self._uid, q["id"], "chat", snapshot, params.user_answer)
        return "已保存到错题集。"

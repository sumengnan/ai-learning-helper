# app/quiz_service.py
from __future__ import annotations

import json


class QuizError(Exception):
    """生成/解析/校验失败。"""


class NoKnowledge(Exception):
    """检索无命中，无法出题。"""


GRADE_SYSTEM = (
    "你是严格的阅卷老师。对照参考答案给学生的简答打分。"
    "只输出 JSON：{\"score\": 0-100 的整数, \"feedback\": \"一句话点评\"}，不要多余文字。")


def _grade_user(question: dict, user_answer) -> str:
    return (f"题目：{question.get('stem', '')}\n参考答案：{question['answer']}\n"
            f"学生作答：{user_answer}\n请打分并点评。")


def _strip_fence(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[: s.rfind("```")]
    return s.strip()


class QuizService:
    def __init__(self, memory, question_store, complete, collection="knowledge",
                 retrieve_k=6, short_pass_score=60) -> None:
        self._memory = memory
        self._store = question_store
        self._complete = complete
        self._collection = collection
        self._retrieve_k = retrieve_k
        self._short_pass_score = short_pass_score

    async def grade(self, question: dict, user_answer) -> dict:
        t = question["type"]
        if t == "single":
            return {"correct": user_answer == question["answer"], "feedback": None}
        if t == "truefalse":
            return {"correct": bool(user_answer) == question["answer"], "feedback": None}
        if t == "multiple":
            correct = sorted(user_answer or []) == sorted(question["answer"])
            return {"correct": correct, "feedback": None}
        if t == "short":
            raw = await self._complete(GRADE_SYSTEM, _grade_user(question, user_answer))
            try:
                verdict = json.loads(_strip_fence(raw))
                score = int(verdict.get("score", 0))
            except (ValueError, TypeError, AttributeError):
                score, verdict = 0, {}
            return {"correct": score >= self._short_pass_score,
                    "score": score, "feedback": verdict.get("feedback")}
        raise QuizError(f"未知题型 {t}")

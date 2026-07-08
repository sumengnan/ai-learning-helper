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


GEN_SYSTEM = (
    "你是出题老师。只依据提供的资料出题，覆盖要点，难度适中。"
    "严格只输出一个 JSON 数组，每个元素形如："
    "{\"type\":\"single|multiple|truefalse|short\",\"stem\":\"题干\","
    "\"options\":[\"选项\"]或null,\"answer\":单选为选项索引整数/多选为索引数组/"
    "判断为true或false/简答为参考答案字符串,\"explanation\":\"解析\"}。"
    "不要输出 JSON 以外的任何文字。")


def _gen_user(topic: str, count: int, types: list[str], context: str) -> str:
    return (f"资料：\n{context}\n\n请就主题「{topic}」出 {count} 道题，"
            f"题型限定在 {types} 中。严格输出 JSON 数组。")


def _valid(q: dict, types: list[str]) -> bool:
    if not isinstance(q, dict):
        return False
    t = q.get("type")
    if t not in types or not (q.get("stem") or "").strip():
        return False
    a = q.get("answer")
    opts = q.get("options")
    if t in ("single", "multiple"):
        if not isinstance(opts, list) or len(opts) < 2:
            return False
        if t == "single":
            return isinstance(a, int) and not isinstance(a, bool) and 0 <= a < len(opts)
        return (isinstance(a, list) and len(a) > 0
                and all(isinstance(i, int) and 0 <= i < len(opts) for i in a))
    if t == "truefalse":
        return isinstance(a, bool)
    if t == "short":
        return isinstance(a, str) and bool(a.strip())
    return False


def _parse_questions(raw: str) -> list:
    try:
        data = json.loads(_strip_fence(raw))
    except (ValueError, TypeError):
        raise QuizError("生成结果不是合法 JSON")
    if not isinstance(data, list):
        raise QuizError("生成结果不是 JSON 数组")
    return data


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

    async def generate(self, topic: str, count: int, types: list[str]) -> list[dict]:
        hits = await self._memory.search(topic, self._collection, self._retrieve_k)
        if not hits:
            raise NoKnowledge(topic)
        context = "\n\n".join(h.text for h in hits)
        raw = await self._complete(GEN_SYSTEM, _gen_user(topic, count, types, context))
        valid = [q for q in _parse_questions(raw) if _valid(q, types)]
        if not valid:
            raise QuizError("生成结果无有效题目")
        for q in valid:
            q["source"] = topic
            q["explanation"] = q.get("explanation", "")
            q["id"] = self._store.create(q)
        return valid

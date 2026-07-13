# app/question_import.py
from __future__ import annotations

from .quiz_service import QuizError, _parse_questions, _valid

ALL_TYPES = ["single", "multiple", "truefalse", "short"]

EXTRACT_SYSTEM = (
    "你是题库整理助手。从用户提供的文本中抽取所有题目，识别题干、选项、正确答案、"
    "解析，并判定题型。严格只输出一个 JSON 数组，每个元素形如："
    "{\"type\":\"single|multiple|truefalse|short\",\"stem\":\"题干\","
    "\"options\":[\"选项\"]或null,\"answer\":单选为选项索引整数/多选为索引数组/"
    "判断为true或false/简答为参考答案字符串,\"explanation\":\"解析\"}。"
    "不要输出 JSON 以外的任何文字。")


def _extract_user(text: str) -> str:
    return f"从下面文本中抽取题目，严格输出 JSON 数组：\n\n{text}"


class QuestionImporter:
    """上传文本 → LLM 抽取结构化题目 → 按(题型+题干)去重 → 入库。不依赖 embedding。"""

    def __init__(self, complete, question_store) -> None:
        self._complete = complete
        self._store = question_store

    async def import_text(self, user_id: str, filename: str, text: str) -> dict:
        raw = await self._complete(EXTRACT_SYSTEM, _extract_user(text))
        try:
            parsed = _parse_questions(raw)
        except QuizError:
            parsed = []
        imported = skipped_invalid = skipped_duplicate = 0
        seen: set[tuple] = set()
        for q in parsed:
            if not _valid(q, ALL_TYPES):
                skipped_invalid += 1
                continue
            key = (q["type"], (q.get("stem") or "").strip())
            if key in seen:                       # 批内去重
                skipped_duplicate += 1
                continue
            seen.add(key)
            q["source"] = filename
            q["explanation"] = q.get("explanation", "")
            if self._store.create_deduped(user_id, q) is None:   # 对库去重
                skipped_duplicate += 1
            else:
                imported += 1
        return {"imported": imported, "skipped_invalid": skipped_invalid,
                "skipped_duplicate": skipped_duplicate}

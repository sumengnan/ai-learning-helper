# app/question_import.py
from __future__ import annotations

import asyncio
import re

from .quiz_service import QuizError, _parse_questions, _valid

ALL_TYPES = ["single", "multiple", "truefalse", "short"]

# 题目起始行：阿拉伯数字/中文数字题号，或「第N题」。用于识别「题目之间」的可切边界。
_Q_START = re.compile(
    r"^\s*(?:\d{1,3}\s*[.、)．]|[（(]\s*\d{1,3}\s*[)）]|第\s*\d{1,3}\s*[题小]"
    r"|[一二三四五六七八九十]{1,3}\s*[、.．])")


def _is_boundary(line: str) -> bool:
    """题目之间的边界：空行，或下一题的题号起始行。"""
    return not line.strip() or bool(_Q_START.match(line))


def _split_text(text: str, chunk_chars: int) -> list[str]:
    """按「题目边界」聚合成多块供并行抽取，保证整题不被拆开（绝不丢题）。

    切点只落在题目之间（空行或题号行）且当前块已达 chunk_chars 时——所以一道题的所有行
    始终留在同一块，不会被边界拆半。若文本无可识别边界（无空行且无题号），则不切分、
    退化为单块（安全：不丢题，只是不加速）。
    """
    lines = text.strip().splitlines()
    chunks: list[str] = []
    cur = ""
    for ln in lines:
        if cur and len(cur) >= chunk_chars and _is_boundary(ln):
            chunks.append(cur)
            cur = "" if not ln.strip() else ln       # 空行边界丢弃，题号行保留为新块首
        elif cur or ln.strip():                       # 跳过块首空行
            cur = f"{cur}\n{ln}" if cur else ln
    if cur.strip():
        chunks.append(cur)
    return chunks or [text]

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
    """上传文本 → LLM 抽取结构化题目 → 按(题型+题干)去重 → 入库。不依赖 embedding。

    长文本切成多块 **并行** 抽取：单次输出短、避免触发模型请求超时重试，且多块并发，
    整体耗时约等于最慢的单块，而非一次生成全部题目的长输出（原先 20 题可耗数分钟）。

    并发有上限（max_concurrency）：无上限地把所有块同时打向单个 LLM 端点，会被服务端
    排队（并行退化为串行），更会撞请求超时触发重试风暴、反而更慢。有闸后既控住峰值并发，
    又保住多块并跑的加速。
    """

    def __init__(self, complete, question_store, chunk_chars: int = 1800,
                 max_concurrency: int = 4) -> None:
        self._complete = complete
        self._store = question_store
        self._chunk_chars = chunk_chars
        self._max_concurrency = max(1, max_concurrency)

    async def _extract(self, text: str) -> list:
        try:
            return _parse_questions(await self._complete(EXTRACT_SYSTEM, _extract_user(text)))
        except QuizError:
            return []

    async def import_text(self, user_id: str, filename: str, text: str) -> dict:
        chunks = _split_text(text, self._chunk_chars)
        if len(chunks) == 1:
            parsed = await self._extract(chunks[0])
        else:
            # 并行抽取（并发受 max_concurrency 上限约束）；单块失败（LLM 报错/坏 JSON）
            # 只丢该块，不影响其余
            sem = asyncio.Semaphore(self._max_concurrency)

            async def _guarded(c: str) -> list:
                async with sem:
                    return await self._extract(c)

            results = await asyncio.gather(
                *(_guarded(c) for c in chunks), return_exceptions=True)
            parsed = [q for r in results if not isinstance(r, BaseException) for q in r]
        # 一次性预载「对库去重」键，之后全程内存判重——避免逐题 SELECT 全表扫。
        # seen 同时承担批内去重与对库去重，两者命中都计入 skipped_duplicate（语义同原先）。
        seen: set[tuple] = self._store.existing_dedup_keys(user_id)
        skipped_invalid = skipped_duplicate = 0
        to_insert: list[dict] = []
        for q in parsed:
            if not _valid(q, ALL_TYPES):
                skipped_invalid += 1
                continue
            key = (q["type"], (q.get("stem") or "").strip())
            if key in seen:                       # 批内 + 对库去重
                skipped_duplicate += 1
                continue
            seen.add(key)
            q["source"] = filename
            q["explanation"] = q.get("explanation", "")
            to_insert.append(q)
        self._store.create_many(user_id, to_insert)   # 单事务批量入库
        return {"imported": len(to_insert), "skipped_invalid": skipped_invalid,
                "skipped_duplicate": skipped_duplicate}

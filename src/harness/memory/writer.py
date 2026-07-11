# src/harness/memory/writer.py
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from .record import MemType, MemoryRecord

log = logging.getLogger(__name__)

_EXTRACT_SYS = (
    "你是记忆提炼器。从下面的文本中提炼**值得长期记住**的事实——"
    "用户偏好、目标、稳定属性、学到的方法或结论；忽略寒暄、一次性内容、临时上下文。"
    "输出 JSON 数组，每个元素：{\"text\": 简洁事实, \"mem_type\": "
    "\"semantic\"|\"episodic\"|\"procedural\", \"entity_key\": 点分实体键如 "
    "\"user.pref.language\"（无明确实体则空串）, \"importance\": 0~1 的浮点}。"
    "没有值得记的内容就输出 []。只输出 JSON，不要解释。"
)


@dataclass
class ExtractedFact:
    text: str
    mem_type: MemType
    entity_key: str = ""
    importance: float = 0.5


@dataclass
class MemoryOp:
    op: str                              # "ADD" | "NOOP" | "REPLACE"
    fact: ExtractedFact | None           # 由 fact_index 解析而来；NOOP 也带（应用时跳过）
    supersede_ids: list[str] = field(default_factory=list)


def _strip_fence(raw: str) -> str:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _parse_facts(raw: str) -> list[ExtractedFact]:
    try:
        data = json.loads(_strip_fence(raw))
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    facts: list[ExtractedFact] = []
    for item in data:
        if not isinstance(item, dict) or not item.get("text"):
            continue
        try:
            mem_type = MemType(item.get("mem_type", "semantic"))
        except ValueError:
            mem_type = MemType.SEMANTIC
        try:
            importance = float(item.get("importance", 0.5))
        except (TypeError, ValueError):
            importance = 0.5
        facts.append(ExtractedFact(
            text=str(item["text"]), mem_type=mem_type,
            entity_key=str(item.get("entity_key") or ""), importance=importance))
    return facts


class MemoryWriter:
    """LLM 驱动的智能写入：提炼 → 找候选 → 调和 → 应用。"""

    def __init__(self, backend, embedder, retriever, complete, *,
                 candidate_k: int = 5) -> None:
        self._backend = backend
        self._embedder = embedder
        self._retriever = retriever
        self._complete = complete
        self._candidate_k = candidate_k

    async def _extract(self, text: str) -> list[ExtractedFact]:
        try:
            raw = await self._complete(_EXTRACT_SYS, text)
        except Exception as e:
            log.warning("memory extract LLM failed: %s", e)
            return []
        return _parse_facts(raw)

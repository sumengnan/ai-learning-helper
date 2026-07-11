# src/harness/memory/writer.py
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from .record import MemType, MemoryFilter, MemoryRecord

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


_RECONCILE_SYS = (
    "你在维护一个记忆库。给你【新事实】列表和与之相关的【已有记忆】。"
    "对每个新事实，决定操作并输出 JSON 数组，元素："
    "{\"op\": \"ADD\"|\"NOOP\"|\"REPLACE\", \"fact_index\": 新事实下标, "
    "\"supersede_ids\": 该事实要作废的已有记忆 id 列表（仅 REPLACE）}。"
    "规则：与某条已有记忆语义等同 → NOOP；是同一实体的更新、或与某条已有记忆矛盾 → "
    "REPLACE 并在 supersede_ids 填被取代的已有记忆 id；全新信息 → ADD。只输出 JSON。"
)


def _parse_ops(raw: str, facts: list[ExtractedFact]) -> list[MemoryOp]:
    try:
        data = json.loads(_strip_fence(raw))
        if not isinstance(data, list):
            raise ValueError("not a list")
    except (json.JSONDecodeError, TypeError, ValueError):
        return [MemoryOp("ADD", f) for f in facts]
    ops: list[MemoryOp] = []
    covered: set[int] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        op = item.get("op")
        idx = item.get("fact_index")
        if op not in ("ADD", "NOOP", "REPLACE"):
            continue
        if not isinstance(idx, int) or not (0 <= idx < len(facts)):
            continue
        covered.add(idx)
        sup = item.get("supersede_ids", []) if op == "REPLACE" else []
        if not isinstance(sup, list):
            sup = []
        ops.append(MemoryOp(op, facts[idx], [str(x) for x in sup]))
    for i, f in enumerate(facts):
        if i not in covered:
            ops.append(MemoryOp("ADD", f))
    return ops


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

    async def _gather_candidates(self, owner_id: str, kind: str,
                                 facts: list[ExtractedFact]) -> list[MemoryRecord]:
        cand: dict[str, MemoryRecord] = {}
        for f in facts:
            if f.entity_key:
                for r in self._backend.list_by_entity(owner_id, kind, f.entity_key):
                    cand[r.id] = r
            hits = await self._retriever.retrieve(
                f.text, MemoryFilter(owner_id=owner_id, kind=kind), self._candidate_k)
            for h in hits:
                cand[h.record.id] = h.record
        return list(cand.values())

    async def _reconcile(self, facts: list[ExtractedFact],
                         candidates: list[MemoryRecord]) -> list[MemoryOp]:
        if not candidates:
            return [MemoryOp("ADD", f) for f in facts]
        payload = json.dumps({
            "new_facts": [{"fact_index": i, "text": f.text} for i, f in enumerate(facts)],
            "existing_memories": [{"id": c.id, "text": c.text, "entity_key": c.entity_key}
                                  for c in candidates],
        }, ensure_ascii=False)
        try:
            raw = await self._complete(_RECONCILE_SYS, payload)
        except Exception as e:
            log.warning("memory reconcile LLM failed: %s", e)
            return [MemoryOp("ADD", f) for f in facts]
        return _parse_ops(raw, facts)

    async def _apply(self, owner_id: str, kind: str,
                     ops: list[MemoryOp]) -> list[str]:
        new_ids: list[str] = []
        for op in ops:
            if op.op == "NOOP" or op.fact is None:
                continue
            try:
                version = 1
                if op.op == "REPLACE" and op.supersede_ids:
                    old = self._backend.get(op.supersede_ids)
                    version = max([r.version for r in old], default=0) + 1
                    self._backend.set_superseded(op.supersede_ids)
                vec = (await self._embedder.embed([op.fact.text]))[0]
                rec = MemoryRecord(
                    owner_id=owner_id, kind=kind, mem_type=op.fact.mem_type,
                    text=op.fact.text, embedding=vec, entity_key=op.fact.entity_key,
                    importance=op.fact.importance, version=version, source="extract")
                new_ids.extend(self._backend.upsert([rec]))
            except Exception as e:                       # 单个 op 失败不影响其余（best-effort）
                log.warning("memory apply op failed (%s): %s", op.op, e)
                continue
        return new_ids

    async def write(self, owner_id: str, kind: str, text: str) -> list[str]:
        """智能写入：提炼→找候选→调和→应用，返回新写入记录 id。best-effort，异常安全。"""
        if not text or not text.strip():
            return []
        facts = await self._extract(text)
        if not facts:
            return []
        try:
            candidates = await self._gather_candidates(owner_id, kind, facts)
        except Exception as e:                           # 找候选失败：降级为无候选（全 ADD）
            log.warning("memory gather candidates failed: %s", e)
            candidates = []
        ops = await self._reconcile(facts, candidates)
        return await self._apply(owner_id, kind, ops)

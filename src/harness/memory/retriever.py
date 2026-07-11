# src/harness/memory/retriever.py
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .record import MemoryFilter, MemoryRecord


def rrf_fuse(ranked_lists: list[list[str]], rrf_k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion：按各路 0-based 排名累加 1/(rrf_k+rank)。无需调参、鲁棒。"""
    scores: dict[str, float] = {}
    for lst in ranked_lists:
        for rank, id_ in enumerate(lst):
            scores[id_] = scores.get(id_, 0.0) + 1.0 / (rrf_k + rank)
    return scores


def recency_score(age_days: float, half_life_days: float) -> float:
    """指数时间衰减，[0,1]。age<=0 记为最新(1.0)，half_life<=0 表示不衰减。"""
    if half_life_days <= 0:
        return 1.0
    return 0.5 ** (max(age_days, 0.0) / half_life_days)


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def mmr_select(candidate_ids: list[str], relevance: dict[str, float],
               embeddings: dict[str, list[float]], lambda_: float, k: int) -> list[str]:
    """Maximal Marginal Relevance：贪心选 λ·rel(d) − (1−λ)·max_{s∈已选} cos(d,s)。"""
    selected: list[str] = []
    remaining = list(candidate_ids)
    while remaining and len(selected) < k:
        best, best_score = None, None
        for c in remaining:
            div = 0.0
            if selected:
                div = max(cosine(embeddings.get(c, []), embeddings.get(s, []))
                          for s in selected)
            score = lambda_ * relevance.get(c, 0.0) - (1.0 - lambda_) * div
            if best_score is None or score > best_score:
                best, best_score = c, score
        selected.append(best)
        remaining.remove(best)
    return selected


@dataclass
class ScoredHit:
    record: MemoryRecord
    score: float
    components: dict = field(default_factory=dict)


@dataclass
class RetrievalConfig:
    candidate_pool: int = 20
    w_relevance: float = 1.0
    w_recency: float = 0.2
    w_importance: float = 0.1
    recency_half_life_days: float = 30.0
    use_keyword: bool = True
    use_mmr: bool = True
    mmr_lambda: float = 0.7
    rrf_k: int = 60


def _age_days(created_at_iso: str, now: datetime) -> float:
    try:
        created = datetime.fromisoformat(created_at_iso)
    except ValueError:
        return 0.0
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (now - created).total_seconds() / 86400.0


def _minmax(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    lo, hi = min(scores.values()), max(scores.values())
    if hi <= lo:
        return {k: 1.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


class Retriever:
    """在 MemoryBackend 之上编排：向量+关键词召回 → RRF → 加权 → MMR → Reranker → top-k。"""

    def __init__(self, backend, embedder, reranker, config: RetrievalConfig,
                 *, now_fn=None) -> None:
        self._backend = backend
        self._embedder = embedder
        self._reranker = reranker
        self._config = config
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    async def retrieve(self, query_text: str, filters: MemoryFilter, k: int,
                       *, config: RetrievalConfig | None = None) -> list[ScoredHit]:
        cfg = config or self._config
        pool = max(k, cfg.candidate_pool)
        query_vec = (await self._embedder.embed([query_text]))[0]
        vec_hits = self._backend.vector_search(
            query_vec, filters=filters, k=pool)
        kw_hits = (self._backend.keyword_search(
            query_text, filters=filters, k=pool) if cfg.use_keyword else [])

        records: dict[str, MemoryRecord] = {}
        for h in vec_hits:
            records[h.record.id] = h.record
        for h in kw_hits:
            records.setdefault(h.record.id, h.record)
        if not records:
            return []

        rel = rrf_fuse([[h.record.id for h in vec_hits],
                        [h.record.id for h in kw_hits]], cfg.rrf_k)
        rel_norm = _minmax(rel)
        now = self._now_fn()
        scored: dict[str, tuple[float, dict]] = {}
        for id_, rec in records.items():
            rec_s = recency_score(_age_days(rec.created_at, now), cfg.recency_half_life_days)
            rel_s = rel_norm.get(id_, 0.0)
            final = (cfg.w_relevance * rel_s + cfg.w_recency * rec_s
                     + cfg.w_importance * rec.importance)
            scored[id_] = (final, {"relevance": rel_s, "recency": rec_s,
                                   "importance": rec.importance})

        order = sorted(records.keys(), key=lambda i: scored[i][0], reverse=True)
        if cfg.use_mmr:
            embs = self._backend.get_embeddings(order)
            order = mmr_select(order, {i: scored[i][0] for i in order},
                               embs, cfg.mmr_lambda, len(order))

        hits = [ScoredHit(record=records[i], score=scored[i][0], components=scored[i][1])
                for i in order]
        hits = await self._reranker.rerank(query_text, hits)
        return hits[:k]

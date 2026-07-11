# src/harness/memory/retriever.py
from __future__ import annotations

import math


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

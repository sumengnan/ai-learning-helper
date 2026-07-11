# tests/test_retriever.py
from datetime import datetime, timedelta, timezone

from harness.memory.record import MemType, MemoryFilter, MemoryRecord
from harness.memory.reranker import NoOpReranker
from harness.memory.retriever import RetrievalConfig, Retriever, ScoredHit
from harness.memory.sqlite_backend import SqliteVecBackend

_NOW = datetime(2026, 7, 11, tzinfo=timezone.utc)


def _iso(days_ago):
    return (_NOW - timedelta(days=days_ago)).isoformat()


def _rec(text, vec, rid, importance=0.5, days_ago=0):
    r = MemoryRecord(owner_id="u1", kind="k", mem_type=MemType.SEMANTIC,
                     text=text, embedding=vec, id=rid, importance=importance)
    r.created_at = _iso(days_ago)
    return r


def _retriever(backend, embedder, config, reranker=None):
    return Retriever(backend, embedder, reranker or NoOpReranker(), config,
                     now_fn=lambda: _NOW)


async def test_retrieve_returns_scored_hits(mock_embedder):
    emb = mock_embedder(dimension=64)
    b = SqliteVecBackend(":memory:", dimension=64)
    for rid, text in [("a", "python programming"), ("b", "the cat sat")]:
        v = (await emb.embed([text]))[0]
        b.upsert([_rec(text, v, rid)])
    cfg = RetrievalConfig(use_keyword=False, use_mmr=False, w_recency=0.0, w_importance=0.0)
    r = _retriever(b, emb, cfg)
    hits = await r.retrieve("cat", MemoryFilter(owner_id="u1"), k=1)
    assert isinstance(hits[0], ScoredHit)
    assert hits[0].record.text == "the cat sat"


async def test_recency_breaks_ties(mock_embedder):
    emb = mock_embedder(dimension=64)
    b = SqliteVecBackend(":memory:", dimension=64)
    v = (await emb.embed(["排序算法"]))[0]
    b.upsert([_rec("排序算法", v, "old", days_ago=60),
              _rec("排序算法", v, "new", days_ago=0)])
    cfg = RetrievalConfig(use_keyword=False, use_mmr=False,
                          w_relevance=1.0, w_recency=1.0, w_importance=0.0,
                          recency_half_life_days=30.0)
    r = _retriever(b, emb, cfg)
    hits = await r.retrieve("排序算法", MemoryFilter(owner_id="u1"), k=2)
    assert hits[0].record.id == "new"


async def test_mmr_diversifies(mock_embedder):
    emb = mock_embedder(dimension=64)
    b = SqliteVecBackend(":memory:", dimension=64)
    va = (await emb.embed(["快速排序快速排序"]))[0]
    vc = (await emb.embed(["图论最短路径"]))[0]
    b.upsert([_rec("快速排序快速排序", va, "a", days_ago=0),
              _rec("快速排序快速排序", va, "b", days_ago=0),
              _rec("图论最短路径", vc, "c", days_ago=0)])
    cfg = RetrievalConfig(use_keyword=False, use_mmr=True, mmr_lambda=0.5,
                          w_recency=0.0, w_importance=0.0)
    r = _retriever(b, emb, cfg)
    ids = [h.record.id for h in await r.retrieve("快速排序快速排序",
                                                 MemoryFilter(owner_id="u1"), k=2)]
    assert "c" in ids


async def test_reranker_is_applied(mock_embedder):
    emb = mock_embedder(dimension=64)
    b = SqliteVecBackend(":memory:", dimension=64)
    for rid, t in [("a", "aaa bbb"), ("b", "ccc ddd")]:
        v = (await emb.embed([t]))[0]
        b.upsert([_rec(t, v, rid)])

    class ReverseReranker:
        async def rerank(self, query, candidates):
            return list(reversed(candidates))

    cfg = RetrievalConfig(use_keyword=False, use_mmr=False)
    base = [h.record.id for h in await _retriever(b, emb, cfg).retrieve(
        "aaa bbb", MemoryFilter(owner_id="u1"), k=2)]
    rev = [h.record.id for h in await _retriever(b, emb, cfg, ReverseReranker()).retrieve(
        "aaa bbb", MemoryFilter(owner_id="u1"), k=2)]
    assert rev == list(reversed(base))


async def test_k_larger_than_pool_not_truncated(mock_embedder):
    emb = mock_embedder(dimension=64)
    b = SqliteVecBackend(":memory:", dimension=64)
    for i in range(30):
        v = (await emb.embed([f"item number {i}"]))[0]
        b.upsert([_rec(f"item number {i}", v, f"r{i}")])
    cfg = RetrievalConfig(candidate_pool=20, use_keyword=False, use_mmr=False,
                          w_recency=0.0, w_importance=0.0)
    hits = await _retriever(b, emb, cfg).retrieve(
        "item number", MemoryFilter(owner_id="u1"), k=25)
    assert len(hits) == 25          # 不被 candidate_pool=20 截断

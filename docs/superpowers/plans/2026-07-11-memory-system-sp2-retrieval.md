> ⚠️ **历史设计记录（已过时）**：harness 内核已抽成外部包
> [ai-harness-framework](https://github.com/sumengnan/ai-harness-framework)（import 名仍是 `harness`）。
> 本文是带日期的设计存档，文中的 `src/harness/` 路径与打包配置反映**当时**的仓库结构、未随抽包更新；
> 当前结构以 [架构文档](../../architecture-harness.md) 为准。

# SP2 检索层 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把 SP1 的纯向量检索升级为 hybrid（向量 + FTS5 trigram 关键词）+ RRF 融合 + recency/importance 加权 + MMR 多样性 + 可插拔 Reranker（默认 no-op），并加最小评测 harness。

**架构：** 在 `SqliteVecBackend` 实装 `keyword_search`（FTS5 trigram 表 `memory_fts`，随写同步，过滤下推）与 `get_embeddings`；新增 `retriever.py`（编排 + 纯函数 rrf/recency/mmr）、`reranker.py`（Protocol + NoOp）、`eval.py`（golden set hit@k/MRR）；`Memory.search` 委托 `Retriever`（默认全开），新增 `Memory.retrieve` 暴露富结果，消费方零改动。

**技术栈：** Python 3.11+、sqlite-vec 0.1.9、SQLite 3.53 FTS5 trigram（已实测中文子串检索 + bm25 可用）、pytest（`asyncio_mode=auto`）、`mock_embedder` fixture（`tests/conftest.py`）。

**设计规格：** `docs/superpowers/specs/2026-07-11-memory-system-sp2-retrieval-design.md`

**工作区注意：** 本计划在 worktree `.claude/worktrees/feat+memory-sp2-retrieval` 执行。跑测试**必须**用 `PYTHONPATH="$PWD/src:$PWD"` 指向本 worktree 的 src，否则会导到主 checkout 的旧 harness。测试命令统一为：
`PYTHONPATH="$PWD/src:$PWD" /Users/sumengnan/PycharmProjects/ai-learning-helper/.venv/bin/python -m pytest <args>`
（下文简写为 `PYTHONPATH=... pytest`。）

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/harness/memory/sqlite_backend.py`（改）| 实装 `keyword_search`（FTS5 trigram）+ `get_embeddings` + `memory_fts` 建表与同步 |
| `src/harness/memory/backend.py`（改）| `MemoryBackend` Protocol 增 `get_embeddings` 签名 |
| `src/harness/memory/reranker.py`（新增）| `Reranker` Protocol + `NoOpReranker` |
| `src/harness/memory/retriever.py`（新增）| `ScoredHit`/`RetrievalConfig`/`Retriever` + 纯函数 `rrf_fuse`/`recency_score`/`cosine`/`mmr_select` |
| `src/harness/memory/eval.py`（新增）| `GoldenCase` + `evaluate`（hit@k/MRR） |
| `src/harness/memory/memory.py`（改）| `Memory.search` 委托 `Retriever`；新增 `Memory.retrieve`；`__init__` 加可选 `retriever` |
| `src/harness/config.py`（改）| RetrievalConfig 相关配置项 |
| `app/assembly.py`（改）| 构造 `Retriever`（config + NoOpReranker）注入 `Memory` |

---

## 任务 1：后端 keyword_search + get_embeddings + memory_fts 同步

**文件：**
- 修改：`src/harness/memory/sqlite_backend.py`
- 修改：`src/harness/memory/backend.py`（Protocol 加 `get_embeddings`）
- 测试：`tests/test_backend_keyword.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_backend_keyword.py
from harness.memory.record import MemType, MemoryFilter, MemoryRecord
from harness.memory.sqlite_backend import SqliteVecBackend


def _rec(owner, kind, text, vec, mem_type=MemType.SEMANTIC, rid=None):
    r = MemoryRecord(owner_id=owner, kind=kind, mem_type=mem_type, text=text, embedding=vec)
    if rid:
        r.id = rid
    return r


def _b():
    return SqliteVecBackend(":memory:", dimension=3)


def test_keyword_cjk_substring():
    b = _b()
    b.upsert([_rec("u1", "k", "二叉树是一种数据结构", [1.0, 0.0, 0.0]),
              _rec("u1", "k", "快速排序是一种排序算法", [0.0, 1.0, 0.0])])
    hits = b.keyword_search("数据结构", filters=MemoryFilter(owner_id="u1"), k=5)
    assert [h.record.text for h in hits] == ["二叉树是一种数据结构"]


def test_keyword_pushdown_filters():
    b = _b()
    b.upsert([_rec("u1", "knowledge", "排序算法讲解", [1.0, 0.0, 0.0]),
              _rec("u1", "conversation", "排序算法讨论", [0.0, 1.0, 0.0]),
              _rec("u2", "knowledge", "排序算法笔记", [0.0, 0.0, 1.0])])
    hits = b.keyword_search("排序算法",
                            filters=MemoryFilter(owner_id="u1", kind="knowledge"), k=5)
    assert [h.record.text for h in hits] == ["排序算法讲解"]   # 只本租户本 kind


def test_keyword_excludes_superseded():
    b = _b()
    r = _rec("u1", "k", "已废弃的排序算法", [1.0, 0.0, 0.0], rid="s1")
    r.superseded = 1
    b.upsert([r])
    assert b.keyword_search("排序算法", filters=MemoryFilter(owner_id="u1"), k=5) == []


def test_keyword_short_or_no_match_empty():
    b = _b()
    b.upsert([_rec("u1", "k", "二叉树", [1.0, 0.0, 0.0])])
    assert b.keyword_search("树", filters=MemoryFilter(owner_id="u1"), k=5) == []   # <3 字 trigram 不成词
    assert b.keyword_search("红黑树", filters=MemoryFilter(owner_id="u1"), k=5) == []  # 无此内容


def test_keyword_deleted_not_matched():
    b = _b()
    b.upsert([_rec("u1", "k", "归并排序算法", [1.0, 0.0, 0.0], rid="d1")])
    b.delete(["d1"])
    assert b.keyword_search("归并排序", filters=MemoryFilter(owner_id="u1"), k=5) == []


def test_get_embeddings_by_id():
    b = _b()
    b.upsert([_rec("u1", "k", "x", [1.0, 0.0, 0.0], rid="a"),
              _rec("u1", "k", "y", [0.0, 1.0, 0.0], rid="c")])
    embs = b.get_embeddings(["a", "c", "missing"])
    assert set(embs.keys()) == {"a", "c"}
    assert embs["a"] == [1.0, 0.0, 0.0]
```

- [ ] **步骤 2：运行验证失败**

运行：`PYTHONPATH=... pytest tests/test_backend_keyword.py -q`
预期：FAIL（`keyword_search` 返回 `[]`、`get_embeddings` 不存在 → AttributeError/断言失败）

- [ ] **步骤 3：实现**

`src/harness/memory/sqlite_backend.py`：

在 `__init__` 建表区（`ix_mem_entity` 索引之后、`self._conn.commit()` 之前）加 FTS5 表：
```python
        self._conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts "
            "USING fts5(text, tokenize='trigram')")
```

在 `upsert` 的写入循环内，`INSERT INTO memory_vec(...)` 之后、`ids.append(r.id)` 之前加：
```python
            self._conn.execute(
                "INSERT INTO memory_fts(rowid, text) VALUES (?, ?)", (rowid, r.text))
```

在 `_delete_rowid` 内追加一行（三表一致）：
```python
        self._conn.execute("DELETE FROM memory_fts WHERE rowid = ?", (rowid,))
```

把占位的 `keyword_search` 替换为：
```python
    def keyword_search(self, query_text: str, *,
                       filters: MemoryFilter, k: int) -> list[MemoryHit]:
        if not query_text or not query_text.strip():
            return []
        conds = ["memory_fts MATCH ?", "r.owner_id = ?"]
        params: list = [query_text, filters.owner_id]
        if filters.kind is not None:
            conds.append("r.kind = ?"); params.append(filters.kind)
        if filters.mem_type is not None:
            conds.append("r.mem_type = ?"); params.append(filters.mem_type)
        if not filters.include_superseded:
            conds.append("r.superseded = ?"); params.append(0)
        cols = ",".join("r." + c for c in _COLS)
        sql = (f"SELECT {cols}, bm25(memory_fts) AS score "
               "FROM memory_fts JOIN memory_records r ON r.rowid = memory_fts.rowid "
               "WHERE " + " AND ".join(conds) + " ORDER BY score LIMIT ?")
        params.append(k)
        try:
            rows = self._conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []   # FTS5 对含特殊字符的查询可能抛语法错 → 关键词侧退化为空
        hits: list[MemoryHit] = []
        for row in rows:
            rec = self._row_to_record(row[:len(_COLS)])
            hits.append(MemoryHit(record=rec, distance=row[len(_COLS)]))
        return hits

    def get_embeddings(self, ids: list[str]) -> dict[str, list[float]]:
        out: dict[str, list[float]] = {}
        for i in ids:
            row = self._conn.execute(
                "SELECT rowid FROM memory_records WHERE id = ?", (i,)).fetchone()
            if row is None:
                continue
            emb = self._conn.execute(
                "SELECT vec_to_json(embedding) FROM memory_vec WHERE rowid = ?",
                (row[0],)).fetchone()
            if emb is not None:
                out[i] = json.loads(emb[0])
        return out
```

`src/harness/memory/backend.py`：在 Protocol 内 `keyword_search` 之后加签名：
```python
    def get_embeddings(self, ids: list[str]) -> dict[str, list[float]]:
        """按记录 id 批量取回向量（MMR 用）。"""
        ...
```

- [ ] **步骤 4：运行验证通过**

运行：`PYTHONPATH=... pytest tests/test_backend_keyword.py tests/test_sqlite_backend.py -q`
预期：PASS（新 6 + SP1 的 10 全绿；确认 FTS5 同步未破坏 upsert/delete）

- [ ] **步骤 5：Commit**

```bash
git add src/harness/memory/sqlite_backend.py src/harness/memory/backend.py tests/test_backend_keyword.py
git commit -m "feat(memory): keyword_search FTS5 trigram + get_embeddings（SP2）"
```

---

## 任务 2：Reranker 接口

**文件：**
- 创建：`src/harness/memory/reranker.py`
- 测试：`tests/test_reranker.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_reranker.py
from harness.memory.reranker import NoOpReranker, Reranker


async def test_noop_preserves_order():
    r = NoOpReranker()
    items = ["a", "b", "c"]
    assert await r.rerank("q", items) == ["a", "b", "c"]


def test_protocol_has_rerank():
    assert hasattr(Reranker, "rerank")
```

- [ ] **步骤 2：运行验证失败**

运行：`PYTHONPATH=... pytest tests/test_reranker.py -q`
预期：FAIL（ModuleNotFoundError）

- [ ] **步骤 3：实现** `src/harness/memory/reranker.py`：

```python
# src/harness/memory/reranker.py
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Reranker(Protocol):
    """精排可插拔切点。默认 NoOpReranker；日后接 LLM/cross-encoder 实现同签名。"""

    async def rerank(self, query: str, candidates: list) -> list:
        ...


class NoOpReranker:
    """不改序，原样返回候选。"""

    async def rerank(self, query: str, candidates: list) -> list:
        return candidates
```

- [ ] **步骤 4：运行验证通过**

运行：`PYTHONPATH=... pytest tests/test_reranker.py -q`
预期：PASS（2 passed）

- [ ] **步骤 5：Commit**

```bash
git add src/harness/memory/reranker.py tests/test_reranker.py
git commit -m "feat(memory): Reranker 可插拔接口 + NoOpReranker（SP2）"
```

---

## 任务 3：检索纯函数（rrf / recency / cosine / mmr）

**文件：**
- 创建：`src/harness/memory/retriever.py`（先只放纯函数，任务 4 再加 Retriever 类）
- 测试：`tests/test_retriever_functions.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_retriever_functions.py
from harness.memory.retriever import cosine, mmr_select, recency_score, rrf_fuse


def test_rrf_fuse_combines_ranks():
    # a 在两路都靠前 → 分最高；c 只在一路
    scores = rrf_fuse([["a", "b"], ["a", "c"]], rrf_k=60)
    assert scores["a"] > scores["b"]
    assert scores["a"] > scores["c"]
    assert set(scores) == {"a", "b", "c"}


def test_recency_score_decays():
    assert recency_score(0.0, 30.0) == 1.0
    assert recency_score(30.0, 30.0) == 0.5        # 一个半衰期
    assert recency_score(60.0, 30.0) == 0.25
    assert recency_score(-5.0, 30.0) == 1.0        # 未来时间钳到 0 age
    assert recency_score(10.0, 0.0) == 1.0         # half_life<=0 → 不衰减


def test_cosine():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0   # 零向量不崩


def test_mmr_select_reduces_redundancy():
    # a 与 b 近重复（同向量），c 正交。相关性 a>b>c。
    rel = {"a": 1.0, "b": 0.9, "c": 0.5}
    embs = {"a": [1.0, 0.0], "b": [1.0, 0.0], "c": [0.0, 1.0]}
    picked = mmr_select(["a", "b", "c"], rel, embs, lambda_=0.5, k=2)
    assert picked[0] == "a"        # 最相关先选
    assert picked[1] == "c"        # 第二个选多样的 c 而非近重复的 b
```

- [ ] **步骤 2：运行验证失败**

运行：`PYTHONPATH=... pytest tests/test_retriever_functions.py -q`
预期：FAIL（ModuleNotFoundError）

- [ ] **步骤 3：实现** `src/harness/memory/retriever.py`（本任务只放纯函数与 imports）：

```python
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
```

- [ ] **步骤 4：运行验证通过**

运行：`PYTHONPATH=... pytest tests/test_retriever_functions.py -q`
预期：PASS（4 passed）

- [ ] **步骤 5：Commit**

```bash
git add src/harness/memory/retriever.py tests/test_retriever_functions.py
git commit -m "feat(memory): 检索纯函数 rrf/recency/cosine/mmr（SP2）"
```

---

## 任务 4：Retriever 编排

**文件：**
- 修改：`src/harness/memory/retriever.py`（加 `ScoredHit`/`RetrievalConfig`/`Retriever`）
- 测试：`tests/test_retriever.py`

- [ ] **步骤 1：编写失败的测试**

```python
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
    b = SqliteVecBackend(":memory:", dimension=64)
    b.upsert([_rec("python programming", [0.0] * 64, "a"),
              _rec("the cat sat", [0.0] * 64, "b")])
    # 用真实 embedder 覆盖零向量：重新按文本 embed 再写入
    emb = mock_embedder(dimension=64)
    for rid, text in [("a", "python programming"), ("b", "the cat sat")]:
        v = (await emb.embed([text]))[0]
        r = _rec(text, v, rid)
        b.upsert([r])
    cfg = RetrievalConfig(use_keyword=False, use_mmr=False, w_recency=0.0, w_importance=0.0)
    r = _retriever(b, emb, cfg)
    hits = await r.retrieve("cat", MemoryFilter(owner_id="u1"), k=1)
    assert isinstance(hits[0], ScoredHit)
    assert hits[0].record.text == "the cat sat"


async def test_recency_breaks_ties(mock_embedder):
    # 两条内容相同（向量、relevance 相同），较新的应靠前
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
              _rec("快速排序快速排序", va, "b", days_ago=0),   # 与 a 近重复
              _rec("图论最短路径", vc, "c", days_ago=0)])
    cfg = RetrievalConfig(use_keyword=False, use_mmr=True, mmr_lambda=0.5,
                          w_recency=0.0, w_importance=0.0)
    r = _retriever(b, emb, cfg)
    ids = [h.record.id for h in await r.retrieve("快速排序快速排序",
                                                 MemoryFilter(owner_id="u1"), k=2)]
    assert "c" in ids            # 多样性项被选入，未被两条近重复挤占


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
```

- [ ] **步骤 2：运行验证失败**

运行：`PYTHONPATH=... pytest tests/test_retriever.py -q`
预期：FAIL（`ImportError: cannot import name 'Retriever'`）

- [ ] **步骤 3：实现**——在 `src/harness/memory/retriever.py` 末尾追加：

```python
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .record import MemoryFilter, MemoryRecord


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
        return {k: 1.0 for k in scores}     # 全相等/单候选 → 1.0，不产生 NaN
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
        query_vec = (await self._embedder.embed([query_text]))[0]
        vec_hits = self._backend.vector_search(
            query_vec, filters=filters, k=cfg.candidate_pool)
        kw_hits = (self._backend.keyword_search(
            query_text, filters=filters, k=cfg.candidate_pool) if cfg.use_keyword else [])

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
```

- [ ] **步骤 4：运行验证通过**

运行：`PYTHONPATH=... pytest tests/test_retriever.py tests/test_retriever_functions.py -q`
预期：PASS（4 + 4）

- [ ] **步骤 5：Commit**

```bash
git add src/harness/memory/retriever.py tests/test_retriever.py
git commit -m "feat(memory): Retriever 编排 hybrid+加权+MMR+rerank（SP2）"
```

---

## 任务 5：Memory 门面委托 Retriever

**文件：**
- 修改：`src/harness/memory/memory.py`
- 测试：`tests/test_memory_retrieve.py`；回归 `tests/test_memory.py`/`test_memory_facade.py`

> `Memory.__init__` 增加**可选** `retriever` 参数（默认 None → 内部用默认 config + NoOpReranker 自建），因此现有 `Memory(backend, embedder, chunk_size, overlap)` 调用**无需改动**。`search` 委托 retriever，映射回旧 `MemoryHit`；新增 `retrieve` 暴露 `ScoredHit`。

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_memory_retrieve.py
from harness.memory.memory import Memory
from harness.memory.retriever import ScoredHit
from harness.memory.sqlite_backend import SqliteVecBackend


def _mem(mock_embedder):
    return Memory(SqliteVecBackend(":memory:", dimension=64),
                  mock_embedder(dimension=64), chunk_size=1000, overlap=0)


async def test_search_still_returns_old_hit_shape(mock_embedder):
    m = _mem(mock_embedder)
    await m.add_texts(["the cat sat on the mat"], "knowledge:u1")
    await m.add_texts(["python programming language"], "knowledge:u1")
    hits = await m.search("cat", "knowledge:u1", k=1)
    assert hits[0].text == "the cat sat on the mat"
    assert hits[0].collection == "knowledge:u1"
    assert hasattr(hits[0], "distance") and hasattr(hits[0], "metadata")


async def test_retrieve_exposes_scored_hits(mock_embedder):
    m = _mem(mock_embedder)
    await m.add_texts(["二叉树是一种数据结构"], "knowledge:u1")
    hits = await m.retrieve("数据结构", "knowledge:u1", k=1)
    assert isinstance(hits[0], ScoredHit)
    assert "relevance" in hits[0].components


async def test_hybrid_recalls_chinese_substring(mock_embedder):
    # mock 向量对无空格中文近乎无效，关键词侧（trigram）应把它召回 → hybrid 生效
    m = _mem(mock_embedder)
    await m.add_texts(["快速排序是最常见的排序算法之一"], "knowledge:u1")
    await m.add_texts(["图的广度优先遍历"], "knowledge:u1")
    hits = await m.search("排序算法", "knowledge:u1", k=1)
    assert hits[0].text == "快速排序是最常见的排序算法之一"
```

- [ ] **步骤 2：运行验证失败**

运行：`PYTHONPATH=... pytest tests/test_memory_retrieve.py -q`
预期：FAIL（`Memory` 无 `retrieve`；`search` 仍走旧 backend.vector_search，中文子串测试可能失败）

- [ ] **步骤 3：实现**——整体重写 `src/harness/memory/memory.py`：

```python
# src/harness/memory/memory.py
from __future__ import annotations

from ..memory.store import MemoryHit          # 旧返回形状，消费方零改动
from .backend import MemoryBackend
from .chunker import chunk
from .embeddings import EmbeddingClient
from .record import MemoryFilter, MemoryRecord, MemType
from .reranker import NoOpReranker
from .retriever import RetrievalConfig, Retriever, ScoredHit


def collection_to_scope(collection: str) -> tuple[str, str]:
    """旧 collection 字符串 → (owner_id, kind)。"<kind>:<owner>" / "<kind>"（无 owner→_global）。"""
    if ":" in collection:
        kind, owner = collection.split(":", 1)
        return owner, kind
    return "_global", collection


class Memory:
    """chunker + embedder + backend + retriever 的门面。add_texts/search 兼容签名。"""

    def __init__(self, backend: MemoryBackend, embedder: EmbeddingClient,
                 chunk_size: int = 1000, overlap: int = 200,
                 retriever: Retriever | None = None) -> None:
        self._backend = backend
        self._embedder = embedder
        self._chunk_size = chunk_size
        self._overlap = overlap
        self._retriever = retriever or Retriever(
            backend, embedder, NoOpReranker(), RetrievalConfig())

    async def add_texts(self, texts: list[str], collection: str,
                        metadata: dict | None = None) -> list[str]:
        all_chunks: list[str] = []
        for t in texts:
            all_chunks.extend(chunk(t, self._chunk_size, self._overlap))
        if not all_chunks:
            return []
        owner_id, kind = collection_to_scope(collection)
        vectors = await self._embedder.embed(all_chunks)
        records = [
            MemoryRecord(owner_id=owner_id, kind=kind, mem_type=MemType.SEMANTIC,
                         text=c, embedding=v, metadata=metadata or {})
            for c, v in zip(all_chunks, vectors)]
        return self._backend.upsert(records)

    async def search(self, query: str, collection: str, k: int) -> list[MemoryHit]:
        owner_id, kind = collection_to_scope(collection)
        hits = await self._retriever.retrieve(
            query, MemoryFilter(owner_id=owner_id, kind=kind), k)
        return [MemoryHit(text=h.record.text, collection=collection,
                          metadata=h.record.metadata, distance=1.0 - h.score)
                for h in hits]

    async def retrieve(self, query: str, collection: str, k: int,
                       *, config: RetrievalConfig | None = None) -> list[ScoredHit]:
        owner_id, kind = collection_to_scope(collection)
        return await self._retriever.retrieve(
            query, MemoryFilter(owner_id=owner_id, kind=kind), k, config=config)
```

- [ ] **步骤 4：运行验证通过**

运行：`PYTHONPATH=... pytest tests/test_memory_retrieve.py tests/test_memory.py tests/test_memory_facade.py -q`
预期：PASS（3 + SP1 的 3 + 3；`search` 兼容形状不变）

- [ ] **步骤 5：Commit**

```bash
git add src/harness/memory/memory.py tests/test_memory_retrieve.py
git commit -m "feat(memory): Memory.search 委托 Retriever + 新增 retrieve（SP2）"
```

---

## 任务 6：评测 harness

**文件：**
- 创建：`src/harness/memory/eval.py`
- 测试：`tests/test_retrieval_eval.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_retrieval_eval.py
from harness.memory.eval import GoldenCase, evaluate
from harness.memory.memory import Memory
from harness.memory.record import MemoryFilter
from harness.memory.reranker import NoOpReranker
from harness.memory.retriever import RetrievalConfig, Retriever
from harness.memory.sqlite_backend import SqliteVecBackend

_CORPUS = [
    ("快速排序是最常见的排序算法之一", "d1"),
    ("二叉树是一种基础数据结构", "d2"),
    ("图的广度优先遍历用于最短路径", "d3"),
    ("动态规划用于求解最优子结构问题", "d4"),
]
_CASES = [
    GoldenCase(query="排序算法", relevant_ids={"d1"}),
    GoldenCase(query="数据结构", relevant_ids={"d2"}),
    GoldenCase(query="广度优先", relevant_ids={"d3"}),
    GoldenCase(query="动态规划", relevant_ids={"d4"}),
]


async def _seed(mock_embedder):
    backend = SqliteVecBackend(":memory:", dimension=64)
    emb = mock_embedder(dimension=64)
    for text, rid in _CORPUS:
        v = (await emb.embed([text]))[0]
        from harness.memory.record import MemType, MemoryRecord
        backend.upsert([MemoryRecord(owner_id="u1", kind="k",
                                     mem_type=MemType.SEMANTIC, text=text,
                                     embedding=v, id=rid)])
    return backend, emb


def _retriever(backend, emb, cfg):
    return Retriever(backend, emb, NoOpReranker(), cfg)


async def test_metrics_computed(mock_embedder):
    backend, emb = await _seed(mock_embedder)
    cfg = RetrievalConfig()
    m = await evaluate(_retriever(backend, emb, cfg), MemoryFilter(owner_id="u1"), _CASES, k=3)
    assert 0.0 <= m["hit@k"] <= 1.0 and 0.0 <= m["mrr"] <= 1.0


async def test_hybrid_beats_vector_only(mock_embedder):
    # 无空格中文使 mock 向量召回很弱；hybrid（含关键词）应 >= 纯向量
    backend, emb = await _seed(mock_embedder)
    vector_only = RetrievalConfig(use_keyword=False, use_mmr=False,
                                  w_recency=0.0, w_importance=0.0)
    hybrid = RetrievalConfig(use_keyword=True, use_mmr=False,
                             w_recency=0.0, w_importance=0.0)
    flt = MemoryFilter(owner_id="u1")
    mv = await evaluate(_retriever(backend, emb, vector_only), flt, _CASES, k=3)
    mh = await evaluate(_retriever(backend, emb, hybrid), flt, _CASES, k=3)
    assert mh["hit@k"] >= mv["hit@k"]
    assert mh["mrr"] >= mv["mrr"]
```

- [ ] **步骤 2：运行验证失败**

运行：`PYTHONPATH=... pytest tests/test_retrieval_eval.py -q`
预期：FAIL（ModuleNotFoundError）

- [ ] **步骤 3：实现** `src/harness/memory/eval.py`：

```python
# src/harness/memory/eval.py
from __future__ import annotations

from dataclasses import dataclass

from .record import MemoryFilter


@dataclass
class GoldenCase:
    query: str
    relevant_ids: set[str]


async def evaluate(retriever, filters: MemoryFilter,
                   cases: list[GoldenCase], k: int) -> dict:
    """在 golden set 上算 hit@k 与 MRR。retriever 需实现 async retrieve(query, filters, k)。"""
    hit_count = 0
    rr_sum = 0.0
    for case in cases:
        results = await retriever.retrieve(case.query, filters, k)
        ids = [h.record.id for h in results]
        if any(i in case.relevant_ids for i in ids):
            hit_count += 1
        for rank, i in enumerate(ids):
            if i in case.relevant_ids:
                rr_sum += 1.0 / (rank + 1)
                break
    n = len(cases) or 1
    return {"hit@k": hit_count / n, "mrr": rr_sum / n}
```

- [ ] **步骤 4：运行验证通过**

运行：`PYTHONPATH=... pytest tests/test_retrieval_eval.py -q`
预期：PASS（2 passed；hybrid ≥ 纯向量）

- [ ] **步骤 5：Commit**

```bash
git add src/harness/memory/eval.py tests/test_retrieval_eval.py
git commit -m "feat(memory): 最小评测 harness golden set hit@k/MRR（SP2）"
```

---

## 任务 7：配置 + assembly 注入 + 全仓回归

**文件：**
- 修改：`src/harness/config.py`（RetrievalConfig 相关项）
- 修改：`app/assembly.py`（构造 Retriever 注入 Memory）
- 测试：全仓回归

- [ ] **步骤 1：先跑相关测试记录基线**

运行：`PYTHONPATH=... pytest tests/app/test_knowledge.py tests/app/test_conversation_memory.py tests/app/test_quiz_generate.py tests/app/test_quiz_api.py tests/app/test_assembly.py tests/test_memory_tools.py -q`
预期：当前全 PASS（作为注入前基线；注入后须仍绿）

- [ ] **步骤 2：加配置**——在 `src/harness/config.py` 的 `search_top_k`/`memory_collection` 附近加：

```python
    retrieval_candidate_pool: int = 20
    retrieval_w_relevance: float = 1.0
    retrieval_w_recency: float = 0.2
    retrieval_w_importance: float = 0.1
    retrieval_recency_half_life_days: float = 30.0
    retrieval_use_keyword: bool = True
    retrieval_use_mmr: bool = True
    retrieval_mmr_lambda: float = 0.7
    retrieval_rrf_k: int = 60
```

- [ ] **步骤 3：assembly 注入**——在 `app/assembly.py` 构造 `Memory` 处（`mem = Memory(mem_store, embedder, config.chunk_size, config.chunk_overlap)`），改为先建 Retriever 再注入：

```python
        from harness.memory.reranker import NoOpReranker
        from harness.memory.retriever import RetrievalConfig, Retriever
        _rcfg = RetrievalConfig(
            candidate_pool=config.retrieval_candidate_pool,
            w_relevance=config.retrieval_w_relevance,
            w_recency=config.retrieval_w_recency,
            w_importance=config.retrieval_w_importance,
            recency_half_life_days=config.retrieval_recency_half_life_days,
            use_keyword=config.retrieval_use_keyword,
            use_mmr=config.retrieval_use_mmr,
            mmr_lambda=config.retrieval_mmr_lambda,
            rrf_k=config.retrieval_rrf_k)
        _retriever = Retriever(mem_store, embedder, NoOpReranker(), _rcfg)
        mem = Memory(mem_store, embedder, config.chunk_size, config.chunk_overlap,
                     retriever=_retriever)
```

- [ ] **步骤 4：跑相关测试 + 全仓回归**

运行（相关）：`PYTHONPATH=... pytest tests/app/test_knowledge.py tests/app/test_conversation_memory.py tests/app/test_quiz_generate.py tests/app/test_quiz_api.py tests/app/test_assembly.py tests/test_memory_tools.py -q`
预期：PASS（与基线一致）

运行（全仓，排除外部依赖）：
```bash
PYTHONPATH="$PWD/src:$PWD" /Users/sumengnan/PycharmProjects/ai-learning-helper/.venv/bin/python -m pytest tests/ -q -p no:cacheprovider \
  --deselect tests/test_integration_real.py \
  --ignore=tests/test_docker_sandbox.py \
  --ignore=tests/test_sandboxed_browser.py \
  --ignore=tests/test_sandboxed_browser_integration.py \
  --ignore=tests/test_browser_playwright.py \
  --ignore=tests/test_browser_integration.py \
  --ignore=tests/test_routing_sandbox.py -k "not real"
```
预期：仅既有的 2 个无关 sandbox 失败（`test_assembly.py::test_no_lang_images_no_multilang_code_tools`、`test_sandbox_manager.py::test_proxy_exposes_sandbox_for_only_when_routing`），其余全绿。

- [ ] **步骤 5：Commit**

```bash
git add src/harness/config.py app/assembly.py
git commit -m "feat(memory): 检索配置 + assembly 注入 Retriever（SP2）"
```

---

## 自检结论

- **规格覆盖**：keyword_search+get_embeddings（T1）、Reranker（T2）、纯函数（T3）、Retriever 编排（T4）、门面委托+retrieve（T5）、评测 harness（T6）、配置+注入（T7）——规格 §1 IN 全覆盖；§1 OUT（重型 rerank / SP3 / SP4）仅留接口。
- **类型一致**：`ScoredHit`/`RetrievalConfig`/`Retriever`（retriever.py）贯穿 T4/T5/T6；`Reranker`（T2）被 T4/T5 用；`get_embeddings` 签名在 backend.py(T1) 与 sqlite_backend.py(T1) 与 Retriever(T4) 一致（按 id）；`Memory.search` 返回旧 `store.MemoryHit`、`Memory.retrieve` 返回 `ScoredHit`，两者显式区分。
- **向后兼容**：`Memory.__init__` 的 `retriever` 为可选默认自建，SP1 现有 `Memory(backend, embedder, …)` 调用与测试无需改；`search` 形状不变，消费方零改动。
- **无占位符**：每步含可运行代码与精确命令；keyword_search 的 `try/except OperationalError` 是对 FTS5 特殊字符查询的显式降级，非缺陷。
- **worktree**：所有测试命令带 `PYTHONPATH="$PWD/src:$PWD"`，规避 harness 可编辑安装的导包陷阱。

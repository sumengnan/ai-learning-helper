> ⚠️ **历史设计记录（已过时）**：harness 内核已抽成外部包
> [ai-harness-framework](https://github.com/sumengnan/ai-harness-framework)（import 名仍是 `harness`）。
> 本文是带日期的设计存档，文中的 `src/harness/` 路径与打包配置反映**当时**的仓库结构、未随抽包更新；
> 当前结构以 [架构文档](../../architecture-harness.md) 为准。

# SP4 遗忘/固化层 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 给记忆系统补齐生命周期管理：TTL 过期（检索生效 + 剔除 + 写入设期）、consolidation（episodic 按相似度聚类、LLM 蒸馏为 semantic）、评测扩展。

**架构：** `SqliteVecBackend.vector_search` 加 `expires_at` 下推过滤（注入 `now_fn`）+ `purge_expired`；`MemoryWriter` 按 `mem_type` 设 `expires_at`；新增 `MemoryMaintainer.maintain()`（按需触发 = 过期剔除 + consolidate）；`eval.py` 加 `store_size`。默认 TTL=0、不自动 maintain → 现有行为不变。

**技术栈：** Python 3.11+、sqlite-vec 0.1.9（`(expires_at=0 OR expires_at>?)` 过滤已实测）、pytest（`asyncio_mode=auto`）、`mock_embedder` + scripted LLM completer + 注入固定 `now_fn`（不打网络）。复用 `backend.list_by_owner/count_by_owner/get_embeddings/set_superseded/upsert`、`build_completer`、SP2 `cosine`。

**设计规格：** `docs/superpowers/specs/2026-07-11-memory-system-sp4-lifecycle-design.md`

**工作区注意：** 本计划在 worktree `.claude/worktrees/feat+memory-sp4-lifecycle` 执行。跑测试**必须**带 `PYTHONPATH="$PWD/src:$PWD"`：
`PYTHONPATH="$PWD/src:$PWD" /Users/sumengnan/PycharmProjects/ai-learning-helper/.venv/bin/python -m pytest <args>`
（下文简写 `PYTHONPATH=... pytest`。）

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/harness/memory/sqlite_backend.py`（改）| `vector_search` 加 expires_at 过滤 + `now_fn`；`purge_expired` |
| `src/harness/memory/backend.py`（改）| Protocol 加 `purge_expired` 签名 |
| `src/harness/memory/writer.py`（改）| `_apply` 按 `mem_type` 设 `expires_at`（`ttl_by_type` + `now_fn`）|
| `src/harness/memory/maintainer.py`（新增）| `MemoryMaintainer` + `ConsolidationConfig` + 贪心聚类 |
| `src/harness/memory/eval.py`（改）| `store_size` 辅助 |
| `app/config.py`（改）| `ttl_*_days`、`consolidation_*` 配置 |
| `app/assembly.py`（改）| 构造 `MemoryMaintainer` 挂 harness + 给 writer 传 `ttl_by_type` |

---

## 任务 1：TTL 检索过滤 + purge_expired

**文件：**
- 修改：`src/harness/memory/sqlite_backend.py`
- 修改：`src/harness/memory/backend.py`
- 测试：`tests/test_backend_ttl.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_backend_ttl.py
from harness.memory.record import MemType, MemoryFilter, MemoryRecord
from harness.memory.sqlite_backend import SqliteVecBackend


def _rec(rid, expires_at, vec=(1.0, 0.0, 0.0)):
    r = MemoryRecord(owner_id="u1", kind="k", mem_type=MemType.SEMANTIC,
                     text=f"m{rid}", embedding=list(vec), id=rid)
    r.expires_at = expires_at
    return r


def _b():
    return SqliteVecBackend(":memory:", dimension=3, now_fn=lambda: 1000)


def test_vector_search_excludes_expired():
    b = _b()
    b.upsert([_rec("never", 0), _rec("expired", 100), _rec("future", 5000)])
    ids = [h.record.id for h in b.vector_search(
        [1.0, 0.0, 0.0], filters=MemoryFilter(owner_id="u1"), k=5)]
    assert "expired" not in ids           # expires_at=100 <= now=1000 → 剔除
    assert "never" in ids and "future" in ids


def test_vector_search_include_expired():
    b = _b()
    b.upsert([_rec("expired", 100)])
    ids = [h.record.id for h in b.vector_search(
        [1.0, 0.0, 0.0], filters=MemoryFilter(owner_id="u1", include_expired=True), k=5)]
    assert ids == ["expired"]


def test_purge_expired_removes_rows():
    b = _b()
    b.upsert([_rec("never", 0), _rec("expired", 100), _rec("future", 5000)])
    n = b.purge_expired(1000)
    assert n == 1                          # 只删 expired
    assert b.get(["expired"]) == []
    assert len(b.get(["never"])) == 1 and len(b.get(["future"])) == 1
```

- [ ] **步骤 2：运行验证失败**

运行：`PYTHONPATH=... pytest tests/test_backend_ttl.py -q`
预期：FAIL（`__init__` 不认 `now_fn`；`purge_expired` 不存在；expired 未被过滤）

- [ ] **步骤 3：实现**

`src/harness/memory/sqlite_backend.py`：
(a) 顶部 import 区加 `import time`（若尚无）。
(b) `__init__` 签名改为带 `now_fn`（keyword-only，默认系统时间；现有位置调用不受影响）：
```python
    def __init__(self, db_path: str, dimension: int, *, now_fn=None) -> None:
        self._dim = dimension
        self._now_fn = now_fn or (lambda: int(time.time()))
        self._conn = sqlite3.connect(db_path)
        # ...（其余不变）
```
(c) `vector_search` 里，在 `if not filters.include_superseded:` 那段**之后**、`# 注：SP1 不过滤 expires_at` 注释处，加 expires_at 过滤（并可删掉那行过时注释）：
```python
        if not filters.include_superseded:
            conds.append("superseded = ?"); params.append(0)
        if not filters.include_expired:
            conds.append("(expires_at = 0 OR expires_at > ?)")
            params.append(self._now_fn())
```
(d) 加 `purge_expired`（放在 `set_superseded` 附近）：
```python
    def purge_expired(self, now: int) -> int:
        rows = self._conn.execute(
            "SELECT rowid FROM memory_records WHERE expires_at != 0 AND expires_at <= ?",
            (now,)).fetchall()
        for (rowid,) in rows:
            self._conn.execute("DELETE FROM memory_records WHERE rowid = ?", (rowid,))
            self._conn.execute("DELETE FROM memory_vec WHERE rowid = ?", (rowid,))
            self._conn.execute("DELETE FROM memory_fts WHERE rowid = ?", (rowid,))
        self._conn.commit()
        return len(rows)
```

`src/harness/memory/backend.py`：Protocol 加签名：
```python
    def purge_expired(self, now: int) -> int:
        """删除已过期记录（expires_at!=0 且 <=now），返回删除条数。"""
        ...
```

- [ ] **步骤 4：运行验证通过**

运行：`PYTHONPATH=... pytest tests/test_backend_ttl.py tests/test_sqlite_backend.py tests/test_backend_keyword.py tests/test_backend_superseded.py -q`
预期：PASS（新 3 + SP1/SP2/SP3 既有全绿；现有数据 expires_at=0 → 过滤全通过，无回归）

- [ ] **步骤 5：Commit**

```bash
git add src/harness/memory/sqlite_backend.py src/harness/memory/backend.py tests/test_backend_ttl.py
git commit -m "feat(memory): TTL 检索过滤 + purge_expired（SP4）"
```

---

## 任务 2：writer 按类型设 TTL

**文件：**
- 修改：`src/harness/memory/writer.py`
- 测试：`tests/test_memory_writer.py`（追加）

> `MemoryWriter.__init__`（约第 113 行 `def __init__(self, backend, embedder, retriever, complete, *, candidate_k=5)`）加两个 keyword-only 参数 `ttl_by_type=None`、`now_fn=None`。`_apply`（约第 158 行）构造 `MemoryRecord`（约第 171 行）时按 `mem_type` 设 `expires_at`。默认 `ttl_by_type=None` → `expires_at=0`，SP3 行为不变。

- [ ] **步骤 1：追加失败测试** 到 `tests/test_memory_writer.py` 末尾

```python
async def test_writer_sets_ttl_by_type(mock_embedder):
    # episodic 配了 TTL → expires_at 非零；semantic 未配 → 0
    backend = SqliteVecBackend(":memory:", dimension=64)
    emb = mock_embedder(dimension=64)
    from harness.memory.reranker import NoOpReranker
    from harness.memory.retriever import RetrievalConfig, Retriever
    retr = Retriever(backend, emb, NoOpReranker(), RetrievalConfig())
    comp = ScriptedCompleter(['[{"text":"用户今天做了练习","mem_type":"episodic"}]'])
    w = MemoryWriter(backend, emb, retr, comp,
                     ttl_by_type={"episodic": 86400}, now_fn=lambda: 1000)
    ids = await w.write("u1", "k", "今天练习了")
    rec = backend.get(ids)[0]
    assert rec.expires_at == 1000 + 86400        # episodic 设了 TTL

    comp2 = ScriptedCompleter(['[{"text":"用户是后端工程师","mem_type":"semantic"}]'])
    w2 = MemoryWriter(backend, emb, retr, comp2,
                      ttl_by_type={"episodic": 86400}, now_fn=lambda: 1000)
    ids2 = await w2.write("u1", "k2", "我是后端")
    assert backend.get(ids2)[0].expires_at == 0  # semantic 未配 → 永不过期


async def test_writer_no_ttl_by_default(mock_embedder):
    backend = SqliteVecBackend(":memory:", dimension=64)
    emb = mock_embedder(dimension=64)
    from harness.memory.reranker import NoOpReranker
    from harness.memory.retriever import RetrievalConfig, Retriever
    retr = Retriever(backend, emb, NoOpReranker(), RetrievalConfig())
    comp = ScriptedCompleter(['[{"text":"用户今天做了练习","mem_type":"episodic"}]'])
    w = MemoryWriter(backend, emb, retr, comp)     # 无 ttl_by_type
    ids = await w.write("u1", "k", "今天练习了")
    assert backend.get(ids)[0].expires_at == 0     # 默认不设 TTL
```

- [ ] **步骤 2：运行验证失败**

运行：`PYTHONPATH=... pytest tests/test_memory_writer.py -k ttl -q`
预期：FAIL（`__init__` 不认 `ttl_by_type`）

- [ ] **步骤 3：实现**——改 `src/harness/memory/writer.py`。

(a) `__init__` 加参数并存字段：
```python
    def __init__(self, backend, embedder, retriever, complete, *,
                 candidate_k: int = 5, ttl_by_type: dict | None = None,
                 now_fn=None) -> None:
        self._backend = backend
        self._embedder = embedder
        self._retriever = retriever
        self._complete = complete
        self._candidate_k = candidate_k
        self._ttl_by_type = ttl_by_type
        import time
        self._now_fn = now_fn or (lambda: int(time.time()))
```
（保留原有字段赋值，只新增 `_ttl_by_type`/`_now_fn` 两行 + 参数。）

(b) `_apply` 里构造 `MemoryRecord` 之前算 `expires_at`，并把它传进 `MemoryRecord(...)`：
```python
                expires_at = 0
                if self._ttl_by_type:
                    ttl = self._ttl_by_type.get(op.fact.mem_type.value, 0)
                    if ttl > 0:
                        expires_at = self._now_fn() + ttl
                vec = (await self._embedder.embed([op.fact.text]))[0]
                rec = MemoryRecord(
                    owner_id=owner_id, kind=kind, mem_type=op.fact.mem_type,
                    text=op.fact.text, embedding=vec, entity_key=op.fact.entity_key,
                    importance=op.fact.importance, version=version, source="extract",
                    expires_at=expires_at)
```

- [ ] **步骤 4：运行验证通过**

运行：`PYTHONPATH=... pytest tests/test_memory_writer.py -q`
预期：PASS（原有 + 新 2 全绿）

- [ ] **步骤 5：Commit**

```bash
git add src/harness/memory/writer.py tests/test_memory_writer.py
git commit -m "feat(memory): writer 按 mem_type 设 TTL（SP4）"
```

---

## 任务 3：MemoryMaintainer 聚类 + consolidate

**文件：**
- 创建：`src/harness/memory/maintainer.py`
- 测试：`tests/test_maintainer.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_maintainer.py
from harness.memory.record import MemType, MemoryFilter, MemoryRecord
from harness.memory.maintainer import ConsolidationConfig, MemoryMaintainer
from harness.memory.sqlite_backend import SqliteVecBackend


class ScriptedCompleter:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
    async def __call__(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        return self._responses.pop(0)


def _epi(rid, vec):
    return MemoryRecord(owner_id="u1", kind="k", mem_type=MemType.EPISODIC,
                        text=f"episodic {rid}", embedding=list(vec), id=rid)


def _maintainer(backend, mock_embedder, responses, **cfg):
    return MemoryMaintainer(backend, mock_embedder(dimension=3),
                            ScriptedCompleter(responses),
                            ConsolidationConfig(**cfg), now_fn=lambda: 1000)


async def test_consolidate_merges_similar(mock_embedder):
    b = SqliteVecBackend(":memory:", dimension=3)
    b.upsert([_epi("a", [1.0, 0.0, 0.0]), _epi("b", [1.0, 0.0, 0.0]),  # a,b 同向量 → 一簇
              _epi("c", [0.0, 1.0, 0.0])])                              # c 孤立
    m = _maintainer(b, mock_embedder, ["用户偏好已固化"], min_cluster=2)
    out = await m.consolidate("u1", "k")
    assert out["clusters"] == 1 and out["merged"] == 2 and out["created"] == 1
    assert b.get(["a"])[0].superseded == 1 and b.get(["b"])[0].superseded == 1  # 源作废
    assert b.get(["c"])[0].superseded == 0                                       # 孤立不动
    # 新 semantic 记录存在
    incl = b.vector_search([1.0, 0.0, 0.0],
                           filters=MemoryFilter(owner_id="u1", mem_type="semantic"), k=5)
    assert any(h.record.source == "consolidate" for h in incl)


async def test_consolidate_skips_isolated(mock_embedder):
    b = SqliteVecBackend(":memory:", dimension=3)
    b.upsert([_epi("a", [1.0, 0.0, 0.0]), _epi("b", [0.0, 1.0, 0.0])])  # 互不相似
    m = _maintainer(b, mock_embedder, [], min_cluster=2)                # 无簇 → 不调 LLM
    out = await m.consolidate("u1", "k")
    assert out["clusters"] == 0
    assert b.get(["a"])[0].superseded == 0 and b.get(["b"])[0].superseded == 0


async def test_consolidate_llm_failure_skips_cluster(mock_embedder):
    b = SqliteVecBackend(":memory:", dimension=3)
    b.upsert([_epi("a", [1.0, 0.0, 0.0]), _epi("b", [1.0, 0.0, 0.0])])

    class Boom:
        async def __call__(self, s, u):
            raise RuntimeError("蒸馏失败")

    m = MemoryMaintainer(b, mock_embedder(dimension=3), Boom(),
                         ConsolidationConfig(min_cluster=2), now_fn=lambda: 1000)
    out = await m.consolidate("u1", "k")           # 不抛异常
    assert out["created"] == 0
    assert b.get(["a"])[0].superseded == 0         # 失败簇的源不作废
```

- [ ] **步骤 2：运行验证失败**

运行：`PYTHONPATH=... pytest tests/test_maintainer.py -q`
预期：FAIL（ModuleNotFoundError）

- [ ] **步骤 3：实现** `src/harness/memory/maintainer.py`：

```python
# src/harness/memory/maintainer.py
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .record import MemType, MemoryRecord
from .retriever import cosine

log = logging.getLogger(__name__)

_DISTILL_SYS = (
    "你是记忆固化器。下面是同一主题的若干条零散情景记忆，请把它们蒸馏合并成"
    "**一条**简洁、稳定的语义事实（中文，一句话）。只输出这条事实文本，不要解释、不要列表。"
)


@dataclass
class ConsolidationConfig:
    sim_threshold: float = 0.85     # 贪心聚类的平均余弦相似度阈值
    min_cluster: int = 2            # 成簇最小成员数
    max_source: int = 200           # 单次 consolidate 处理的 episodic 上限


class MemoryMaintainer:
    """按需记忆维护：过期剔除 + consolidation（episodic 蒸馏为 semantic）。"""

    def __init__(self, backend, embedder, complete,
                 config: ConsolidationConfig, *, now_fn=None) -> None:
        self._backend = backend
        self._embedder = embedder
        self._complete = complete
        self._config = config
        self._now_fn = now_fn or (lambda: int(time.time()))

    def _cluster(self, records: list[MemoryRecord],
                 embs: dict[str, list[float]]) -> list[list[MemoryRecord]]:
        clusters: list[list[MemoryRecord]] = []
        for r in records:
            v = embs.get(r.id)
            if v is None:
                continue
            placed = False
            for cl in clusters:
                sims = [cosine(v, embs[m.id]) for m in cl if m.id in embs]
                if sims and sum(sims) / len(sims) >= self._config.sim_threshold:
                    cl.append(r)
                    placed = True
                    break
            if not placed:
                clusters.append([r])
        return clusters

    async def consolidate(self, owner_id: str, kind: str) -> dict:
        records = [r for r in self._backend.list_by_owner(owner_id, kind)
                   if r.mem_type == MemType.EPISODIC][:self._config.max_source]
        if not records:
            return {"clusters": 0, "merged": 0, "created": 0}
        embs = self._backend.get_embeddings([r.id for r in records])
        clusters = self._cluster(records, embs)
        nclusters = merged = created = 0
        for cl in clusters:
            if len(cl) < self._config.min_cluster:
                continue
            try:
                text = (await self._complete(
                    _DISTILL_SYS, "\n".join(m.text for m in cl))).strip()
            except Exception as e:
                log.warning("consolidate distill failed: %s", e)
                continue
            if not text:
                continue
            try:
                vec = (await self._embedder.embed([text]))[0]
                self._backend.upsert([MemoryRecord(
                    owner_id=owner_id, kind=kind, mem_type=MemType.SEMANTIC,
                    text=text, embedding=vec, source="consolidate")])
                self._backend.set_superseded([m.id for m in cl])
            except Exception as e:
                log.warning("consolidate apply failed: %s", e)
                continue
            nclusters += 1
            merged += len(cl)
            created += 1
        return {"clusters": nclusters, "merged": merged, "created": created}

    async def maintain(self, owner_id: str, kind: str) -> dict:
        purged = self._backend.purge_expired(self._now_fn())
        result = await self.consolidate(owner_id, kind)
        return {"purged": purged, **result}
```

- [ ] **步骤 4：运行验证通过**

运行：`PYTHONPATH=... pytest tests/test_maintainer.py -q`
预期：PASS（3 passed）

- [ ] **步骤 5：Commit**

```bash
git add src/harness/memory/maintainer.py tests/test_maintainer.py
git commit -m "feat(memory): MemoryMaintainer 聚类 + consolidate（SP4）"
```

---

## 任务 4：maintain 端到端 + store_size 评测扩展

**文件：**
- 修改：`src/harness/memory/eval.py`
- 测试：`tests/test_maintainer.py`（追加）、`tests/test_retrieval_eval.py`（追加）

- [ ] **步骤 1：编写失败的测试**

追加到 `tests/test_maintainer.py`：
```python
async def test_maintain_purges_and_consolidates(mock_embedder):
    b = SqliteVecBackend(":memory:", dimension=3, now_fn=lambda: 1000)
    # 一条已过期（会被 purge）
    exp = _epi("old", [0.0, 0.0, 1.0]); exp.expires_at = 100
    b.upsert([exp, _epi("a", [1.0, 0.0, 0.0]), _epi("b", [1.0, 0.0, 0.0])])
    m = _maintainer(b, mock_embedder, ["固化事实"], min_cluster=2)
    out = await m.maintain("u1", "k")
    assert out["purged"] == 1 and out["created"] == 1
    assert b.get(["old"]) == []                    # 过期已删
```

追加到 `tests/test_retrieval_eval.py`：
```python
from harness.memory.eval import store_size
from harness.memory.maintainer import ConsolidationConfig, MemoryMaintainer


class _Scripted:
    def __init__(self, responses):
        self._r = list(responses)
    async def __call__(self, s, u):
        return self._r.pop(0)


async def test_consolidation_preserves_recall_reduces_size(mock_embedder):
    from harness.memory.record import MemType, MemoryFilter, MemoryRecord
    from harness.memory.reranker import NoOpReranker
    from harness.memory.retriever import RetrievalConfig, Retriever
    backend = SqliteVecBackend(":memory:", dimension=64)
    emb = mock_embedder(dimension=64)
    distilled = "用户偏好深色主题"
    v = (await emb.embed(["深色 主题 偏好"]))[0]
    for rid in ("a", "b", "c"):                    # 3 条同向量 episodic
        backend.upsert([MemoryRecord(owner_id="u1", kind="k", mem_type=MemType.EPISODIC,
                                     text=f"深色 主题 偏好 {rid}", embedding=v, id=rid)])
    size_before = store_size(backend, "u1", "k")
    assert size_before == 3
    m = MemoryMaintainer(backend, emb, _Scripted([distilled]),
                         ConsolidationConfig(min_cluster=2), now_fn=lambda: 1000)
    await m.consolidate("u1", "k")
    size_after = store_size(backend, "u1", "k")
    assert size_after < size_before                # 记录数下降（3 源作废 + 1 新）
    # 固化后的语义记忆仍可召回（用蒸馏文本作 query，向量精确匹配）
    retr = Retriever(backend, emb, NoOpReranker(), RetrievalConfig())
    hits = await retr.retrieve(distilled, MemoryFilter(owner_id="u1", kind="k"), k=5)
    assert any(h.record.text == distilled for h in hits)
```

- [ ] **步骤 2：运行验证失败**

运行：`PYTHONPATH=... pytest tests/test_maintainer.py -k maintain tests/test_retrieval_eval.py -k consolidation -q`
预期：FAIL（`store_size` 不存在）

- [ ] **步骤 3：实现**——`src/harness/memory/eval.py` 末尾加：

```python
def store_size(backend, owner_id: str, kind: str) -> int:
    """命名空间内未废弃记录数（评测「固化降噪」用）。"""
    return backend.count_by_owner(owner_id, kind)
```

- [ ] **步骤 4：运行验证通过**

运行：`PYTHONPATH=... pytest tests/test_maintainer.py tests/test_retrieval_eval.py -q`
预期：PASS（maintainer 全部 + eval 全部绿）

- [ ] **步骤 5：Commit**

```bash
git add src/harness/memory/eval.py tests/test_maintainer.py tests/test_retrieval_eval.py
git commit -m "feat(memory): maintain 端到端 + store_size 评测扩展（SP4）"
```

---

## 任务 5：config + assembly 注入 + 全仓回归

**文件：**
- 修改：`app/config.py`、`app/assembly.py`
- 测试：全仓回归

> 默认 TTL 全 0、maintain 不自动跑 → 现有行为不变。`MemoryMaintainer` 挂 `harness.memory_maintainer` 供 app 按需调用；`ttl_by_type`（config 天数×86400，0 跳过）传给已有的 `MemoryWriter` 构造。

- [ ] **步骤 1：先跑基线**

运行：`PYTHONPATH=... pytest tests/app/test_assembly.py tests/app/test_api.py tests/app/test_conversation_memory.py -q`
记录 PASS 数。

- [ ] **步骤 2：加配置**——`app/config.py`（attachment/memory_write 配置附近）：
```python
    ttl_episodic_days: int = 0
    ttl_semantic_days: int = 0
    ttl_procedural_days: int = 0
    consolidation_sim_threshold: float = 0.85
    consolidation_min_cluster: int = 2
    consolidation_max_source: int = 200
```

- [ ] **步骤 3：assembly 注入**——`app/assembly.py`。已知结构：记忆装配在 `if config.api_key or config.embedding_api_key:` 块内，块前有 `memory`/`memory_store`/`memory_writer` 的 `= None` 初始化；`MemoryWriter(...)` 在 `if config.memory_write_extract:` 内构造；`Harness` dataclass 与 `return Harness(...)` 见 SP3 加的字段。

(a) 块前加 `memory_maintainer = None`（与 `memory_writer = None` 同级）。

(b) 构造 `ttl_by_type` 并传给 `MemoryWriter`——把 `if config.memory_write_extract:` 内那句 `memory_writer = MemoryWriter(mem_store, embedder, _retriever, build_completer(client, config.model), candidate_k=config.memory_write_candidate_k)` 改为带 `ttl_by_type`：
```python
            _ttl_by_type = {
                "episodic": config.ttl_episodic_days * 86400,
                "semantic": config.ttl_semantic_days * 86400,
                "procedural": config.ttl_procedural_days * 86400,
            }
            memory_writer = MemoryWriter(
                mem_store, embedder, _retriever,
                build_completer(client, config.model),
                candidate_k=config.memory_write_candidate_k,
                ttl_by_type=_ttl_by_type)
```
（`ttl>0` 才生效，默认天数 0 → 全 0 → 不设 TTL，行为不变。）

(c) 记忆装配块内、`mem = Memory(...)` 之后加 MemoryMaintainer 构造：
```python
        from harness.memory.maintainer import ConsolidationConfig, MemoryMaintainer
        memory_maintainer = MemoryMaintainer(
            mem_store, embedder, build_completer(client, config.model),
            ConsolidationConfig(
                sim_threshold=config.consolidation_sim_threshold,
                min_cluster=config.consolidation_min_cluster,
                max_source=config.consolidation_max_source))
```

(d) `Harness` dataclass 加字段 `memory_maintainer: object | None = None`；`return Harness(...)` 加 `memory_maintainer=memory_maintainer`。

- [ ] **步骤 4：跑相关 + 全仓回归**

相关：`PYTHONPATH=... pytest tests/app/test_assembly.py tests/app/test_api.py tests/app/test_conversation_memory.py tests/test_backend_ttl.py tests/test_maintainer.py tests/test_memory_writer.py -q`（预期与基线一致 + SP4 全绿）

全仓（排除外部依赖）：
```bash
PYTHONPATH="$PWD/src:$PWD" /Users/sumengnan/PycharmProjects/ai-learning-helper/.venv/bin/python -m pytest tests/ -q -p no:cacheprovider \
  --deselect tests/test_integration_real.py \
  --ignore=tests/test_docker_sandbox.py --ignore=tests/test_sandboxed_browser.py \
  --ignore=tests/test_sandboxed_browser_integration.py --ignore=tests/test_browser_playwright.py \
  --ignore=tests/test_browser_integration.py --ignore=tests/test_routing_sandbox.py -k "not real"
```
预期：仅既有 2 个无关 sandbox 失败（`test_assembly.py::test_no_lang_images_no_multilang_code_tools`、`test_sandbox_manager.py::test_proxy_exposes_sandbox_for_only_when_routing`），其余全绿。

- [ ] **步骤 5：Commit**

```bash
git add app/config.py app/assembly.py
git commit -m "feat(memory): TTL/consolidation 配置 + assembly 注入 MemoryMaintainer（SP4）"
```

---

## 自检结论

- **规格覆盖**：TTL 检索过滤+purge（T1）、writer TTL（T2）、maintainer 聚类+consolidate（T3）、maintain 端到端+store_size 评测（T4）、config+assembly（T5）——规格 §1 IN 全覆盖；§1 OUT（内置调度/跨命名空间/跨类型合并）不实现。
- **类型一致**：`MemoryMaintainer`/`ConsolidationConfig`（maintainer.py）贯穿 T3/T4/T5；`purge_expired(now)->int` 在 backend.py(T1)/sqlite_backend.py(T1)/maintain(T3) 一致；`now_fn` 在 backend/writer/maintainer 均为可注入 keyword（默认系统时间）；consolidation 产出 `source="consolidate"` 的 semantic、`set_superseded` 源 episodic。
- **向后兼容**：backend `now_fn`、writer `ttl_by_type`/`now_fn`、maintainer 全为新增 keyword 默认参数或新类；默认 TTL=0（`(expires_at=0 OR ...)` 全通过）、不自动 maintain → SP1/SP2/SP3 行为字节级不变。
- **无占位符**：每步含可运行代码与命令；best-effort 跳过、聚类阈值是规格明确行为。
- **worktree**：所有命令带 `PYTHONPATH="$PWD/src:$PWD"`。

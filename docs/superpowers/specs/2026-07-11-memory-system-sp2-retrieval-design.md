> ⚠️ **历史设计记录（已过时）**：harness 内核已抽成外部包
> [ai-harness-framework](https://github.com/sumengnan/ai-harness-framework)（import 名仍是 `harness`）。
> 本文是带日期的设计存档，文中的 `src/harness/` 路径与打包配置反映**当时**的仓库结构、未随抽包更新；
> 当前结构以 [架构文档](../../architecture-harness.md) 为准。

# 生产级记忆系统 · SP2 检索层 设计规格

- **日期**：2026-07-11
- **状态**：待实现（brainstorming 已定稿）
- **定位**：小规模多用户，SQLite 为主、接口留可替换切点、评测一等公民
- **前置**：SP1 记忆内核已合并到 `main`（类型化 `MemoryRecord`、`SqliteVecBackend` 分区隔离+metadata 下推、`MemoryBackend` 接口、`Memory` 门面兼容、`keyword_search` 留空占位）

---

## 0. 背景与范围

SP1 把存储与隔离做对了，但检索仍是**纯向量相似度**：无关键词、无 recency/importance 加权、无多样性、无精排。SP2 在 SP1 的 `MemoryBackend` 之上把检索升级为**多路召回 + 加权打分 + 多样性 + 可插拔精排**，并加最小评测量化质量。

**本规格只覆盖 SP2。** SP3（写入提炼/upsert-by-entity/矛盾处理）、SP4（TTL/consolidation/评测扩展）不在内。

**关键决策（brainstorming 已定）**：
- rerank **可插拔接口 + 默认 no-op**（零新基建；重型精排留待需要时）
- 评测用**手写小型 golden set** + hit@k/MRR（确定性、不打网络）
- `Memory.search` **默认全开** hybrid+加权+MMR（开箱即优于纯向量）
- 融合用 **RRF**（无需调参、鲁棒）；recency 用**指数时间衰减**；MMR 平衡相关性与去冗余

**已实测能力（本 worktree，sqlite 3.53.1）**：FTS5 编译在内；`tokenize='trigram'` 对中文做子串检索有效（查询需 ≥3 字符，短查询由向量兜底），`bm25()` 排序可用。

---

## 1. 范围与验收

### IN
1. **`SqliteVecBackend.keyword_search` 实装**：FTS5 trigram 表 `memory_fts`，随 upsert/delete 同步；检索时 owner_id/kind/mem_type/superseded **SQL 下推过滤**，`bm25()` 排序。
2. **`SqliteVecBackend.get_embeddings(ids)`**：按记录 id 批量取回候选向量（供 MMR 算两两相似度）。
3. **`Retriever`** 单元：向量+关键词召回 → RRF 融合 → recency/importance 加权 → MMR 多样性 → 可插拔 Reranker → top-k。
4. **`Reranker` Protocol + `NoOpReranker`**（默认）。
5. **`RetrievalConfig`**：候选池、权重、recency 半衰期、MMR λ、开关、reranker。
6. **门面**：`Memory.search` 委托 Retriever（默认全开），映射回旧 `MemoryHit`；新增 `Memory.retrieve` 暴露富结果（分项分）。
7. **最小评测 harness**：golden set + hit@k/MRR runner + fixture 测试（断言 hybrid≥纯向量、MMR 提升多样性）。

### OUT（预留/后续）
- 重型 rerank 实现（LLM/cross-encoder）—— 只留接口。
- 写入提炼/打分/upsert-by-entity/矛盾（SP3）；TTL/consolidation/评测扩展（SP4）。
- 查询改写/扩展（不做）。

### 验收标准
1. `keyword_search` 对中文（≥3 字）能召回含该子串的记录，且遵守 owner/kind/mem_type/superseded 过滤（SQL 下推，非 Python 后过滤）；查询 <3 字或无匹配时返回空、不报错。
2. `Retriever.retrieve` 在同时有向量与关键词命中的场景下，RRF 融合结果同时受两路影响；关键词侧空时退化为纯向量、不报错。
3. recency 加权：较新记录在相关性相近时排更前（可用固定时间戳构造验证）。
4. MMR：候选含近重复项时，top-k 的冗余低于不开 MMR（用重复向量构造验证）。
5. Reranker 可插拔：注入一个「反转顺序」的假 reranker 能改变最终序；默认 NoOpReranker 不改序。
6. `Memory.search` 返回旧 `MemoryHit(text, collection, metadata, distance)` 形状，`QuizService`/`ConversationMemoryService`/agent 工具**零改动**通过其现有测试。
7. 评测 harness：在手写 golden set 上，hybrid 配置的 hit@k/MRR **≥** 纯向量配置。
8. 全仓既有测试不回归（含 SP1 全部记忆测试）。

---

## 2. 架构与模块

```
src/harness/memory/
├── retriever.py     [新增] Retriever + ScoredHit + RetrievalConfig + RRF/recency/MMR 纯函数
├── reranker.py      [新增] Reranker Protocol + NoOpReranker
├── eval.py          [新增] golden set 评测：hit@k / MRR runner
├── sqlite_backend.py[改]   实装 keyword_search（FTS5 trigram）+ get_embeddings + memory_fts 同步
├── memory.py        [改]   Memory.search 委托 Retriever；新增 Memory.retrieve
├── backend.py       [改]   MemoryBackend Protocol 增加 get_embeddings 签名
└── record.py        [不变]
app/
├── assembly.py      [改]   构造 Retriever（注入 RetrievalConfig + NoOpReranker）并挂到 Memory
└── config.py / src/harness/config.py [改] RetrievalConfig 相关配置项
```

### 2.1 keyword_search（FTS5 trigram）

建表（`SqliteVecBackend.__init__` 增加）：
```sql
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(text, tokenize='trigram');
```
`memory_fts` 的 rowid 与 `memory_records.rowid` 对齐（同 vec 表的对齐手法）。
- **同步**：`upsert` 写入 companion + vec 后，再 `INSERT INTO memory_fts(rowid, text) VALUES(?, ?)`；`_delete_rowid` 增加 `DELETE FROM memory_fts WHERE rowid=?`（三表一致）。
- **检索**：
```sql
SELECT r.rowid, bm25(memory_fts) AS score
FROM memory_fts JOIN memory_records r ON r.rowid = memory_fts.rowid
WHERE memory_fts MATCH ? AND r.owner_id = ?
  [AND r.kind = ?] [AND r.mem_type = ?] AND r.superseded = 0
ORDER BY score LIMIT ?
```
过滤条件在 SQL 内（下推）；回填走 `_row_to_record`。返回 `list[MemoryHit]`（distance=bm25 分，仅供内部排名，不对外语义化）。
- **短查询/无匹配**：FTS5 MATCH 无结果或查询 <3 字（trigram 无法成词）→ 返回 `[]`。Retriever 据此退化。

### 2.2 get_embeddings

```python
def get_embeddings(self, ids: list[str]) -> dict[str, list[float]]:
    # JOIN memory_records r ON r.rowid = memory_vec.rowid
    # SELECT r.id, vec_to_json(memory_vec.embedding) WHERE r.id IN (...)
```
按**记录 id**（非内部 rowid）取回向量，供 MMR 计算候选两两余弦相似度。用 id 与公共 API（get/delete 均按 id）一致，Retriever 全程用 `record.id` 作候选键，不暴露 rowid。

### 2.3 Retriever 流水线

```python
@dataclass
class ScoredHit:
    record: MemoryRecord
    score: float                 # 最终总分（越大越相关）
    components: dict              # {"relevance":.., "recency":.., "importance":.., "rank_vec":.., "rank_kw":..} 供评测/调试

@dataclass
class RetrievalConfig:
    candidate_pool: int = 20     # 每路召回数
    w_relevance: float = 1.0
    w_recency: float = 0.2
    w_importance: float = 0.1
    recency_half_life_days: float = 30.0
    use_keyword: bool = True
    use_mmr: bool = True
    mmr_lambda: float = 0.7      # 越大越偏相关性
    rrf_k: int = 60

class Retriever:
    def __init__(self, backend, embedder, reranker, config, *, now_fn): ...
    async def retrieve(self, query_text, filters, k, *, config=None) -> list[ScoredHit]: ...
```

**步骤（纯函数尽量拆出以便单测）**：
1. `query_vec = await embedder.embed([query_text])`；`vec_hits = backend.vector_search(query_vec, filters, k=pool)`。
2. `kw_hits = backend.keyword_search(query_text, filters, k=pool)`（`use_keyword` 且查询非空）。
3. **RRF 融合**：对每个候选，`rel = Σ 1/(rrf_k + rank_i)`（rank_i 为其在该路的 0-based 排名；未出现的路不计）。得候选集 relevance 分。
4. **加权打分**：`recency = 0.5 ** (age_days / half_life)`（age 由 `now_fn() - created_at`）；`importance = record.importance`；`final = w_rel·rel_norm + w_rec·recency + w_imp·importance`（rel_norm 为 relevance 的 min-max 归一）。
5. **MMR**（`use_mmr`）：`get_embeddings([c.record.id for c in 候选])` 取候选向量，迭代选出使 `λ·final(d) − (1−λ)·max_{s∈已选} cos(d,s)` 最大的 d，直到 k 个。
6. **Reranker**：`reranker.rerank(query_text, candidates)`（默认 NoOp 原序返回）。
7. 返回前 k 个 `ScoredHit`。

**纯函数拆分**（`retriever.py` 内，独立可测）：`rrf_fuse(lists)`、`recency_score(age_days, half_life)`、`mmr_select(candidates, embeddings, lambda, k)`。

### 2.4 Reranker

```python
class Reranker(Protocol):
    async def rerank(self, query: str, candidates: list[ScoredHit]) -> list[ScoredHit]: ...

class NoOpReranker:
    async def rerank(self, query, candidates): return candidates
```
日后 LLM/cross-encoder reranker 实现同 Protocol，Retriever 无感知。

### 2.5 门面兼容

- `Memory.__init__` 增加可选 `retriever`（None 时内部用默认 RetrievalConfig + NoOpReranker 构造一个，保证独立可用）。
- `Memory.search(query, collection, k)`：`filters = MemoryFilter(*collection_to_scope(collection))` → `retriever.retrieve` → 映射 `ScoredHit` 为旧 `MemoryHit(text=record.text, collection=collection, metadata=record.metadata, distance=1-normalized_score)`，**按最终序返回**。消费方零改动（只用 text/metadata + 顺序）。
- 新增 `Memory.retrieve(query, collection, k, *, config=None) -> list[ScoredHit]`：暴露富结果供评测与高级调用。

### 2.6 评测 harness（`eval.py`）

```python
@dataclass
class GoldenCase:
    query: str
    relevant_ids: set[str]       # 期望命中的 MemoryRecord.id

async def evaluate(retriever, filters, cases, k) -> dict:  # {"hit@k":.., "mrr":..}
```
- `hit@k`：top-k 命中任一 relevant 的比例；`mrr`：首个命中的倒数排名均值。
- fixture 测试：构造一小组记录 + golden cases，跑两种 config（纯向量 vs hybrid），断言 hybrid 的 hit@k/MRR ≥ 纯向量；另构造近重复项断言 MMR 降冗余。

### 2.7 配置注入

`AppConfig`/`HarnessConfig` 增加：`retrieval_candidate_pool`、`retrieval_w_relevance/w_recency/w_importance`、`retrieval_recency_half_life_days`、`retrieval_use_keyword`、`retrieval_use_mmr`、`retrieval_mmr_lambda`、`retrieval_rrf_k`。`app/assembly.py` 据此构造 `RetrievalConfig` + `Retriever`（reranker=NoOpReranker）注入 `Memory`。

---

## 3. 错误处理与边界

- **keyword 空/短查询**：`keyword_search` 返回 `[]`，RRF 只用向量一路，结果等价纯向量，不报错。
- **MMR/rerank 候选不足 k**：有多少返回多少。
- **now_fn 注入**：recency 依赖当前时间；用可注入 `now_fn`（默认 `datetime.now(timezone.utc)`）使测试可用固定时间构造（避免测试里直接调 now 造成不确定）。
- **FTS5 同步一致性**：`memory_fts` 写入与 companion/vec 在同一 `upsert` 内、末尾一次 `commit`；沿用 SP1 的整批预校验，异常时零写入，三表不脱节。
- **归一化除零**：relevance 全相等或单候选时 min-max 归一退化为 1.0（不产生 NaN）。

---

## 4. 测试策略（TDD，用 mock_embedder，不打网络）

1. `test_keyword_search`：中文 trigram 召回 + owner/kind/mem_type/superseded 下推过滤 + 短查询空。
2. `test_retriever_rrf`：两路排名融合正确（构造已知两路顺序验证融合序）。
3. `test_retriever_recency`：相关性相近时较新记录靠前（固定 now_fn + created_at）。
4. `test_retriever_mmr`：近重复候选下 MMR 降冗余。
5. `test_reranker_pluggable`：反转 reranker 改变序；NoOp 不改序。
6. `test_memory_search_compat`：返回旧 MemoryHit 形状；`test_knowledge`/`test_conversation_memory`/quiz 现有测试全绿。
7. `test_eval_harness`：hit@k/MRR 计算正确；hybrid ≥ 纯向量。
8. 全仓回归（含 SP1 记忆测试）。

---

## 5. 交付顺序（TDD，供 writing-plans 细化）

1. `keyword_search` + `memory_fts` 同步 + `get_embeddings`（backend）
2. `reranker.py`（Protocol + NoOp）
3. `retriever.py` 纯函数（rrf/recency/mmr）+ 单测
4. `Retriever.retrieve` 编排 + 单测
5. `Memory` 门面委托 + `Memory.retrieve` + 兼容测试
6. `eval.py` + fixture 评测测试
7. 配置 + assembly 注入 + 全仓回归

---

## 6. 未决/预留
- reranker 具体实现（LLM/cross-encoder）→ 需要时新子任务，只实现 `Reranker` 接口。
- 权重默认值（w_rel=1.0/w_rec=0.2/w_imp=0.1、half_life=30d、mmr_λ=0.7）为初值，可后续按评测调；SP2 只保证「hybrid≥纯向量」。
- 评测 golden set 扩展、CI 回归门 → SP4。

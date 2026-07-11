# 生产级记忆系统 · SP4 遗忘/固化层 设计规格

- **日期**：2026-07-11
- **状态**：待实现（brainstorming 已定稿）
- **定位**：小规模多用户，SQLite 为主、接口留可替换切点、评测一等公民
- **前置**：SP1 内核 + SP2 检索 + SP3 写入提炼已合并到 `main`

---

## 0. 背景与范围

记忆系统已有**存储(SP1)+检索(SP2)+写入(SP3)**三块，但缺**生命周期管理**：`expires_at` 字段建了但恒 0（TTL 从未生效）、记忆只增不减、episodic 记忆不会固化为 semantic、近重复记忆累积成噪声。SP4 补齐这最后一块，让记忆系统完整。

**关键决策（brainstorming 已定）**：
- 触发：**按需 `MemoryMaintainer.maintain(owner_id, kind)`**，无内置调度器（app 从 cron/endpoint 调用）。过期过滤在检索时实时生效。
- 范围：**全套收官**——TTL/过期 + consolidation（含压缩）+ 评测扩展。
- consolidation：**只 episodic → semantic**（人类记忆固化经典路线）；semantic/procedural 不动。默认关时行为不变（安全回退）。
- LLM 复用 `build_completer`；相似度复用 SP2 `cosine`；列举源记忆复用已有 `backend.list_by_owner`。

**已实测能力**：vec0 metadata 列支持 `(expires_at=0 OR expires_at>?)` 的 OR+比较过滤下推（剔除已过期行），故 TTL 检索过滤可进 KNN。

**本规格只覆盖 SP4**，是记忆系统四子项目的收官。

---

## 1. 范围与验收

### IN
1. **TTL 检索生效**：`SqliteVecBackend.vector_search` KNN 加 `(expires_at=0 OR expires_at>now)`（`not include_expired` 时），`now` 由注入的 `now_fn`（默认 `time.time`）提供。
2. **TTL 写入**：`MemoryWriter` 按 `mem_type` 给新记录 `expires_at`（config 的 per-type TTL；0=永不）。
3. **过期剔除**：`SqliteVecBackend.purge_expired(now)` 删除已过期行（records/vec/fts 三表一致）。
4. **`MemoryMaintainer`**：`maintain(owner_id, kind)` = 过期剔除 + consolidation。
5. **Consolidation**：episodic 记忆按 embedding 相似度贪心聚类 → 每个 ≥2 成员簇 LLM 蒸馏为 1 条 semantic → 写入新 semantic + `set_superseded` 源 episodic。
6. **评测扩展**（eval.py）：`store_size(backend, owner_id, kind)`；fixture 测试断言 consolidation 后**召回不降 + 记录数下降**。
7. **稳健**：LLM/embedder 失败 → 该簇跳过、记日志，不影响其余（best-effort）。

### OUT（预留/后续）
- 内置调度/后台任务（app 层用 cron 触发，不在库内）。
- 跨 kind/跨 owner 全局 consolidation（只按单命名空间）。
- 跨类型（semantic 之间）合并（本期只 episodic→semantic）。
- 真实 LLM 蒸馏质量的大规模量化（fixture 级评测即可）。

### 验收标准
1. TTL 检索：已过期记录（`expires_at!=0 且 <=now`）被 `vector_search` 默认排除；`include_expired=True` 或 `expires_at=0` 时可见；现有 `expires_at=0` 数据全部照常（**向后兼容**）。
2. TTL 写入：`MemoryWriter` 在配置了 per-type TTL 时给 episodic 事实设非零 `expires_at`；TTL 关（默认 0）时恒 0，SP3 行为不变。
3. `purge_expired`：删除已过期行后，三表无残留、`get` 取不到、检索取不到。
4. Consolidation：≥2 条相似 episodic → 产出 1 条 semantic（源 episodic 全部 `superseded=1`、可 `include_superseded` 找回）；孤立 episodic（无相似簇）不动。
5. 评测：consolidation 后同一 golden set 的 hit@k **不低于**consolidation 前，且 `store_size` 下降。
6. 稳健：某簇 LLM 蒸馏失败 → 跳过该簇（源不被 supersede、不产出），其余簇正常，`maintain` 不抛异常。
7. 默认关（未配置 TTL、未调 `maintain`）时，SP1/SP2/SP3 行为与现有测试逐字节一致。
8. 全仓既有测试不回归。

---

## 2. 架构与模块

```
src/harness/memory/
├── maintainer.py    [新增] MemoryMaintainer（maintain/consolidate + 贪心聚类）
├── sqlite_backend.py[改]   vector_search 加 expires_at 过滤 + now_fn；purge_expired
├── backend.py       [改]   Protocol 加 purge_expired 签名
├── writer.py        [改]   _apply 按 mem_type 设 expires_at（TTL）
├── eval.py          [改]   store_size 辅助
└── (record/retriever/reranker/memory 不变)
app/
├── config.py        [改]   ttl_*_days、consolidation_* 配置
└── assembly.py      [改]   构造 MemoryMaintainer 挂 harness（供 app 按需调用）
```

### 2.1 TTL 检索过滤（sqlite_backend.py）

`SqliteVecBackend.__init__` 加可注入 `now_fn`（默认 `lambda: int(time.time())`，测试可传固定值）。`vector_search` 在 `not filters.include_expired` 时追加：
```python
        if not filters.include_expired:
            conds.append("(expires_at = 0 OR expires_at > ?)")
            params.append(self._now_fn())
```
（`MemoryFilter.include_expired` 字段 SP1 已建。现有数据 expires_at=0 → 全通过。）

### 2.2 purge_expired（sqlite_backend.py + backend.py）

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
backend.py Protocol 加 `purge_expired(now) -> int` 签名。

### 2.3 TTL 写入（writer.py）

`MemoryWriter.__init__` 增加可选 `ttl_by_type: dict[str, int] | None`（{mem_type_value: 秒数}，缺省 None=不设 TTL）+ `now_fn`。`_apply` 构造 `MemoryRecord` 时：
```python
        expires_at = 0
        if self._ttl_by_type:
            ttl = self._ttl_by_type.get(op.fact.mem_type.value, 0)
            if ttl > 0:
                expires_at = self._now_fn() + ttl
        rec = MemoryRecord(..., expires_at=expires_at, ...)
```
默认 `ttl_by_type=None` → `expires_at=0` → SP3 行为不变。

### 2.4 MemoryMaintainer（maintainer.py）

```python
@dataclass
class ConsolidationConfig:
    sim_threshold: float = 0.85     # 贪心聚类的余弦相似度阈值
    min_cluster: int = 2            # 成簇最小成员数
    max_source: int = 200           # 单次 consolidate 处理的 episodic 上限（防超大命名空间）

class MemoryMaintainer:
    def __init__(self, backend, embedder, complete, config, *, now_fn=None): ...

    async def maintain(self, owner_id: str, kind: str) -> dict:
        purged = self._backend.purge_expired(self._now())
        consolidated = await self.consolidate(owner_id, kind)
        return {"purged": purged, **consolidated}

    async def consolidate(self, owner_id: str, kind: str) -> dict:
        # 1. 取未废弃 episodic（复用 list_by_owner + 过滤 mem_type，上限 max_source）
        # 2. get_embeddings 取向量 → 贪心聚类（cosine >= sim_threshold）
        # 3. 每个 >=min_cluster 的簇：LLM 蒸馏簇内文本 → 1 条 semantic 事实
        #    写入新 semantic MemoryRecord（version=1, source="consolidate"）
        #    set_superseded(簇内源 id)
        # 返回 {"clusters": n, "merged": 源条数, "created": 新条数}
```

- **聚类**：贪心——遍历 episodic，对每条找已建簇中平均相似度 ≥ 阈值的加入，否则新建簇。用 SP2 `cosine` + `backend.get_embeddings`。确定性（按 rowid/id 排序遍历）。
- **蒸馏**：`complete(_DISTILL_SYS, 簇内文本拼接)` → 1 条简洁 semantic 事实文本；LLM 失败/空 → 跳过该簇（不 supersede、不产出）。
- **列举源**：复用 `backend.list_by_owner(owner_id, kind)`（返回未废弃记录），在 maintainer 内 `filter mem_type==EPISODIC`，取前 `max_source` 条。

### 2.5 评测扩展（eval.py）

加 `store_size(backend, owner_id, kind) -> int`（复用 `backend.count_by_owner`）。fixture 测试：建若干相似 episodic + golden case → 测 consolidation 前后 hit@k 与 store_size，断言 `hit@k_after >= hit@k_before` 且 `size_after < size_before`。

### 2.6 集成（assembly.py + config.py）
- config：`ttl_episodic_days: int = 0`（0=不过期，默认关）、`ttl_semantic_days: int = 0`、`ttl_procedural_days: int = 0`、`consolidation_sim_threshold: float = 0.85`、`consolidation_min_cluster: int = 2`、`consolidation_max_source: int = 200`。
- assembly：构造 `MemoryMaintainer` 挂 `harness.memory_maintainer`（供 app 按需调用）；把 `ttl_by_type` 传给 `MemoryWriter`（config 的 per-type days 换算成秒，0 则该类型不设 TTL）。
- 触发：SP4 只交付 `MemoryMaintainer` 类 + 挂载；实际 cron/endpoint 由 app 后续接（薄钩子，非本规格核心）。

---

## 3. 错误处理与边界

- **now 注入**：backend / writer / maintainer 均用可注入 `now_fn`（默认系统时间），测试传固定值，避免不确定。
- **consolidation best-effort**：单簇 LLM/embedder 失败 → 记日志跳过该簇，其余继续；`maintain` 不抛异常。
- **孤立记忆**：无相似簇（<min_cluster）的 episodic 不动，不误合并。
- **蒸馏产出为空**：跳过该簇，源不 supersede。
- **max_source 上限**：超大命名空间只处理前 N 条，`log` 说明截断（避免一次 consolidate 打爆 LLM 预算）。
- **向后兼容**：TTL 默认全 0、maintain 不自动跑 → 现有行为字节级不变。

---

## 4. 测试策略（TDD，mock LLM + mock_embedder，不打网络）

1. `test_vector_search_excludes_expired`：过期记录被默认排除，include_expired/永不过期可见（注入固定 now_fn）。
2. `test_purge_expired`：删除已过期行，三表无残留。
3. `test_writer_sets_ttl_by_type`：配置 episodic TTL → episodic 事实 expires_at 非零；未配置 → 0。
4. `test_consolidate_merges_similar_episodics`：≥2 相似 episodic → 1 条 semantic，源 superseded。
5. `test_consolidate_skips_isolated`：孤立 episodic 不动。
6. `test_consolidate_llm_failure_skips_cluster`：蒸馏失败 → 该簇源保留、不产出，不抛异常。
7. `test_maintain_purges_and_consolidates`：端到端 maintain 返回统计。
8. `test_eval_consolidation_preserves_recall`：consolidation 后 hit@k 不降、store_size 下降。
9. 默认关行为不变 + 全仓回归。

---

## 5. 交付顺序（TDD，供 writing-plans 细化）

1. `vector_search` expires_at 过滤 + `now_fn` + `purge_expired`（backend + Protocol）+ 测试
2. `MemoryWriter` TTL（ttl_by_type + now_fn）+ 测试
3. `MemoryMaintainer` 聚类 + consolidate（mock LLM）+ 测试
4. `maintain` 端到端 + `store_size` 评测扩展 + 测试
5. config + assembly 注入 + 全仓回归

---

## 6. 未决/预留
- 蒸馏/聚类阈值默认值（sim 0.85、min_cluster 2、TTL 天数）为初值，可后续按评测调；SP4 保证机制正确 + 安全降级。
- 内置调度、跨命名空间/跨类型合并、真实 LLM 质量量化 → 库外/后续。
- 记忆系统四子项目至此收官：存储(SP1)→检索(SP2)→写入(SP3)→生命周期(SP4)。

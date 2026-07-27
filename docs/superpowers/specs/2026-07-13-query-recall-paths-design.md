> ⚠️ **历史设计记录（已过时）**：harness 内核已抽成外部包
> [ai-harness-framework](https://github.com/sumengnan/ai-harness-framework)（import 名仍是 `harness`）。
> 本文是带日期的设计存档，文中的 `src/harness/` 路径与打包配置反映**当时**的仓库结构、未随抽包更新；
> 当前结构以 [架构文档](../../architecture-harness.md) 为准。

# 查询期召回增强（实体键 / 多查询 / HyDE）设计

日期：2026-07-13
状态：已确认，待实现

## 背景与目标

记忆检索（`src/harness/memory/retriever.py`）当前只有两路召回：稠密向量 + FTS5 关键词，经 `rrf_fuse` 融合。这是强基线，但对「按属性精确取值」「问法与记忆表述错配」「换个说法」等场景有召回盲区。本次新增三路查询期召回增强，扩大召回覆盖（而非只调排序）：

1. **实体键召回**：LLM 从 query 抽取点分实体键（如 `user.pref.language`），用 `backend.list_by_entity` 精确取该实体的记忆。
2. **多查询扩展**：LLM 把 query 改写成 N 条近义/子问题，各自跑向量召回。
3. **HyDE**：LLM 生成假设答案文档，用其向量做召回。

已确认的约束：
- 三路都需要**查询期 LLM 调用**。为省延迟，三者**合并为一次 LLM 调用**（QueryPlanner）。
- 三路**各自 env 开关、默认全关**，保证零行为变更。
- 检索在聊天首字关键路径上（`build_manager` 在流式前 await），故 LLM 调用**必须带超时 + 失败降级**回基础两路。

## 现状与可复用件

- `Retriever.retrieve(query_text, filters, k, *, config)`（`retriever.py:104`）：`vector_search + keyword_search → rrf_fuse → _minmax → 加权 → mmr → reranker`。`rrf_fuse` 已支持 **N 路** ranked list，多路融合零改造。
- `RetrievalConfig`（`retriever.py:61`）：dataclass，字段由 `config.retrieval_*` 在 `app/assembly.py` 注入。
- Backend（`sqlite_backend.py`）：`vector_search/keyword_search -> list[MemoryHit]`（`.record` + `.distance`）；`list_by_entity(owner_id, kind, entity_key) -> list[MemoryRecord]`（裸记录、无分数、排除 superseded）。
- `MemoryFilter`：`owner_id`(必填)/`kind`/`mem_type`/`include_superseded`/`include_expired`。**无 entity_key 维度**——实体召回走 `list_by_entity` 旁路。
- `build_completer(client, model)`（`app/completion.py`）→ `async (system, user) -> str`；写入侧 `MemoryWriter` 已这样拿 LLM，照抄注入 `Retriever`。
- 测试：`tests/test_memory_writer.py` 的 `ScriptedCompleter`（async 假 completer）可复用；`tests/test_retrieval_eval.py` 的 `hit@k`/`mrr` 对比模板。

## 详细设计

### 组件 A：QueryPlanner（新文件 `src/harness/memory/query_planner.py`）

一次 LLM 调用产出所有启用特性所需产物。

```python
@dataclass
class QueryPlan:
    variants: list[str]      # 多查询改写；未启用为 []
    hypothetical: str        # HyDE 假设文档；未启用为 ""
    entity_keys: list[str]   # 抽取的实体键；未启用为 []

EMPTY_PLAN = QueryPlan([], "", [])

class QueryPlanner:
    def __init__(self, complete, *, multi_query=False, multi_query_n=3,
                 hyde=False, entity=False, timeout_s=2.0): ...
    @property
    def enabled(self) -> bool:            # complete 存在且至少一路开
        ...
    async def plan(self, query_text) -> QueryPlan: ...
```

- `plan()`：未 enabled → 直接返回 `EMPTY_PLAN`（不调 LLM）。否则按开启项**动态拼提示**，要求 LLM 只输出含启用字段的 JSON 对象。`asyncio.wait_for(complete(...), timeout_s)` 包超时；超时/异常/解析失败 → `EMPTY_PLAN`（降级）。
- 解析鲁棒：`_strip_fence` + `json.loads`；取字段、`variants` 截到 `multi_query_n`、非法/缺失字段回退默认。

### 组件 B：Retriever 集成

- `Retriever.__init__(..., complete=None)`：存 `self._complete`。缺省 None → 三路自动跳过（零行为变更、测试免 LLM）。
- `retrieve()` 改造（保持后半段加权/MMR/reranker 不变）：
  1. `planner = QueryPlanner(self._complete, multi_query=cfg.use_multi_query, multi_query_n=cfg.multi_query_n, hyde=cfg.use_hyde, entity=cfg.use_entity_recall, timeout_s=cfg.query_plan_timeout_s)`；`plan = await planner.plan(query_text)`。
  2. **批量 embedding**：`embed([query_text] + plan.variants + ([plan.hypothetical] if plan.hypothetical else []))` 一次算完。
  3. 组多路 ranked list 喂 `rrf_fuse`：
     - 基础：`vector(query_vec)` + `keyword(query)`（后者受 `use_keyword`）
     - 多查询：每条 variant 的向量各跑一次 `vector_search`
     - HyDE：假设文档向量跑一次 `vector_search`
     - 实体键：`filters.kind` 非空时，每个 entity_key 跑 `list_by_entity(filters.owner_id, filters.kind, key)`，返回记录按 `created_at` 倒序排成一路；`kind` 为空则跳过
  4. 所有命中的 record（含实体裸记录）并入 `records` 字典；`rrf_fuse(所有 ranked lists, cfg.rrf_k)` → 后续不变。

### 组件 C：配置与装配

- `RetrievalConfig` 增字段（默认全关/安全值）：`use_entity_recall=False`、`use_multi_query=False`、`use_hyde=False`、`multi_query_n=3`、`query_plan_timeout_s=2.0`。
- `app/config.py` 增 `HARNESS_RETRIEVAL_*` env（照现有 `retrieval_*` 模式）：entity_recall / multi_query / hyde / multi_query_n / query_plan_timeout_s。
- `app/assembly.py`：构造 `RetrievalConfig` 时带上新字段；`Retriever(..., complete=build_completer(client, config.model))`。

## 数据流

`retrieve(q)` → QueryPlanner 一次 LLM（超时保护）得 `{variants, hypothetical, entity_keys}` → 批量 embed → 基础两路 + 多查询各路 + HyDE 一路 + 实体各路 → `rrf_fuse` N 路融合 → `_minmax` → relevance/recency/importance 加权 → MMR → reranker → top-k。三路全关或 LLM 失败时退化为原始两路，结果与现状一致。

## 错误处理

- QueryPlanner：超时/LLM 异常/JSON 非法 → `EMPTY_PLAN`，检索照常走基础两路，仅 `log.warning`。
- 实体路：`filters.kind` 为空 → 跳过（不报错）。
- 单路 `vector_search`/`list_by_entity` 异常不应炸掉整轮——用已有的 try/降级风格保护（best-effort）。
- 全程不改变「检索失败不阻断聊天」的上层 try/except 语义。

## 测试

- `tests/test_query_planner.py`（新）：未 enabled 不调 LLM 返回 EMPTY_PLAN；正常解析 variants/hypothetical/entity_keys（含 `multi_query_n` 截断）；超时→EMPTY_PLAN（用一个 sleep 超时的假 completer）；坏 JSON→EMPTY_PLAN。用 `ScriptedCompleter` 模式。
- `tests/test_retriever.py`（增）：借 mock_embedder「同文本→同向量」的确定性——
  - 多查询：基础 query 命不中的文档，作为 variant 时被召回。
  - HyDE：假设文档文本=某库中文档文本 → 该文档被召回。
  - 实体键：带 `entity_key` 的记录，planner 返回该键 + `filters.kind` 非空 → 命中（向量/关键词都不命中时）。
  - 降级：`complete=None` → 结果与基础两路一致（回归）。
- `tests/test_retrieval_eval.py`（增，轻量）：仿 `test_hybrid_beats_vector_only`，用 `_Scripted` 假 planner 断言「开某路的 hit@k ≥ 关」。

## YAGNI 取舍

- 三路合并为一次 LLM 调用，不做三次独立调用。
- 多查询只对 variant 跑向量（不跑关键词）——改写对稠密召回收益最大，避免 DB 调用翻倍。
- QueryPlanner 按 `cfg` 每次 retrieve 构造（极轻），从而尊重 per-call config 覆盖；不做全局单例。
- 不引入新的 LLM 客户端；复用 `build_completer`。

# 生产级记忆系统 · SP3 写入提炼层 设计规格

- **日期**：2026-07-11
- **状态**：待实现（brainstorming 已定稿）
- **定位**：小规模多用户，SQLite 为主、接口留可替换切点
- **前置**：SP1 记忆内核 + SP2 检索层已合并到 `main`

---

## 0. 背景与范围

SP1 把存储/隔离做对了，SP2 把检索做强了，但**写入仍是机械转储**：`ConversationMemoryService.record_turn` 直接把整轮原文 `add_texts` 入库，`mem_type` 恒 `semantic`，无提炼、无分类、无打分、无去重、无更新、无矛盾处理——SP1 建好的 `entity_key/version/superseded/importance`、三种 `mem_type` 至今没被真正使用。

SP3 把写入升级为 **LLM 驱动的智能写入**：从文本提炼值得记的事实、判定类型、打重要性分、去重、按实体更新（而非追加）、检测并消解矛盾。这是从 RAG 迈向「记忆」的关键跃迁。

**本规格只覆盖 SP3。** SP4（TTL/consolidation/评测扩展）不在内。

**关键决策（brainstorming 已定）**：
- 触发：构建 `MemoryWriter` 引擎，接入对话每轮写入点，**config 门控、默认关**（开则每轮一次提炼）。默认关时保持 SP1 原文入库行为（安全回退）。
- 矛盾消解：**自动新胜**——旧记忆标 `superseded=1`（行保留可恢复），新记忆生效。用上 SP1 的 `superseded/version`。
- 操作规划：**两次 LLM 调用**——提炼(LLM#1) + 批量调和(LLM#2)。成本有界、可测。
- 复用件：SP2 `Retriever`（找语义相关旧记忆）、`backend.list_by_entity/upsert/get`、`app/completion.py` 的 `build_completer`。

---

## 1. 范围与验收

### IN
1. **`MemoryWriter`**：`write(owner_id, kind, text)` 流水线——提炼 → 找候选 → 调和 → 应用。
2. **提炼(LLM#1)**：文本 → 结构化事实 `[{text, mem_type, entity_key, importance}]`；无干货返回 `[]`。
3. **找候选**：实体精确匹配（`backend.list_by_entity`）+ 语义近邻（SP2 `Retriever`）→ 候选旧记忆集。
4. **调和(LLM#2)**：新事实 + 候选旧记忆 → 操作列表 `[{op: ADD|NOOP|REPLACE, ...}]`。
5. **应用（确定性）**：ADD/REPLACE 新事实 → `backend.upsert` 成 `MemoryRecord`；REPLACE 的旧 id → `backend.set_superseded`。
6. **`SqliteVecBackend.set_superseded(ids)`** + backend.py Protocol 签名。
7. **集成**：`ConversationMemoryService` 增智能写入路径，config 门控默认关。
8. **稳健性**：LLM/JSON 解析失败 → 记日志、安全跳过（不写垃圾、不打断聊天）。

### OUT（预留/后续）
- TTL 过期、consolidation（情景→语义合并）、评测扩展（SP4）。
- 跨会话/全局记忆合并、记忆图谱（不做）。
- 重型/本地提炼模型（只用注入的 `complete`）。

### 验收标准
1. 提炼：给定含事实的文本，产出结构化事实（类型/实体/重要性）；纯寒暄产出 `[]`、不写入。
2. 去重（NOOP）：新事实与已存在记忆语义等同 → 不新增。
3. upsert-by-entity（REPLACE）：同 `entity_key` 的更新事实 → 旧记忆 `superseded=1`、新记忆 `version=旧+1` 写入。
4. 矛盾消解（REPLACE）：新事实与旧记忆冲突 → 旧标 superseded、新生效。
5. 类型/重要性落库：提炼判定的 `mem_type`、`importance` 正确写入 `MemoryRecord`。
6. `set_superseded`：指定 id 的记录 `superseded` 在 `memory_records` 与 `memory_vec` 均置 1，之后被 SP2 检索默认排除。
7. 稳健：LLM 抛错或返回非法 JSON → 该次写入跳过、不抛异常、不产生半写入。
8. 默认关：`memory_write_extract=False` 时 `ConversationMemoryService` 行为与 SP1 逐字节一致；现有测试全绿。
9. 全仓既有测试不回归。

---

## 2. 架构与模块

```
src/harness/memory/
├── writer.py        [新增] MemoryWriter + ExtractedFact + MemoryOp + 提炼/调和 prompt + JSON 解析
├── sqlite_backend.py[改]   set_superseded(ids)
├── backend.py       [改]   Protocol 加 set_superseded 签名
└── (record/retriever/reranker/memory 不变)
app/
├── conversation_memory.py [改] 增智能写入路径（config 门控），默认走原 record_turn
├── config.py              [改] memory_write_extract 等配置
└── assembly.py            [改] 构造 MemoryWriter 注入 ConversationMemoryService（启用时）
```

### 2.1 数据类型（writer.py）

```python
@dataclass
class ExtractedFact:
    text: str
    mem_type: MemType            # episodic | semantic | procedural
    entity_key: str = ""         # 如 "user.pref.language"；无实体则 ""
    importance: float = 0.5

@dataclass
class MemoryOp:
    op: str                      # "ADD" | "NOOP" | "REPLACE"
    fact: ExtractedFact | None   # ADD/REPLACE 的新事实（由 fact_index 解析而来）；NOOP 为 None
    supersede_ids: list[str]     # REPLACE 要作废的旧记忆 id；ADD/NOOP 为 []
```

调和 LLM 输出用 `fact_index`（指向提炼事实列表的下标）而非重发事实文本——解析时按 index 解析成 `ExtractedFact` 填入 `MemoryOp.fact`，避免 LLM 复述文本引入偏差。越界/非法 index 的 op 丢弃。

### 2.2 MemoryWriter 流水线

```python
class MemoryWriter:
    def __init__(self, backend, embedder, retriever, complete, *,
                 candidate_k: int = 5): ...
    # embedder：把 ADD/REPLACE 的新事实文本 embed 成向量再 upsert
    # complete：async (system_prompt, user_prompt) -> str，由 build_completer 提供

    async def write(self, owner_id: str, kind: str, text: str) -> list[str]:
        # 1. 提炼
        facts = await self._extract(text)                 # LLM#1 → list[ExtractedFact]；空则返回 []
        if not facts:
            return []
        # 2. 找候选（实体精确 + 语义近邻，去重成集合）
        candidates = await self._gather_candidates(owner_id, kind, facts)
        # 3. 调和
        ops = await self._reconcile(facts, candidates)    # LLM#2 → list[MemoryOp]
        # 4. 应用（确定性）
        return self._apply(owner_id, kind, ops)
```

- **`_extract`**：`complete(_EXTRACT_SYS, text)` → JSON 数组解析成 `ExtractedFact`；解析失败/空 → `[]`（记日志）。prompt 要求：只提炼持久、值得长期记住的事实（用户偏好、目标、稳定属性、学到的方法），忽略寒暄/一次性内容；给每条判类型、实体键（点分路径或空）、重要性 0-1。
- **`_gather_candidates`**：对每个 fact，`backend.list_by_entity(owner_id, kind, fact.entity_key)`（entity_key 非空时）+ `retriever.retrieve(fact.text, MemoryFilter(owner_id, kind), k=candidate_k)`；按 id 去重，取回各候选的 `MemoryRecord`。
- **`_reconcile`**：`complete(_RECONCILE_SYS, payload)`，payload 含新事实列表 + 候选旧记忆（id/text/entity_key）。LLM 输出 ops JSON：每条 `{op, fact_index 或 fact, supersede_ids}`。解析失败 → 降级为「全部 ADD」（保底不丢新事实，也不误删旧的）。
- **`_apply`**：确定性执行——
  - `ADD`/`REPLACE` 的 fact → `MemoryRecord(owner_id, kind, mem_type, text, entity_key, importance, embedding=<embed>, version=<旧最高+1 或 1>, source="extract")` → `backend.upsert`。
  - `REPLACE` 的 `supersede_ids` → `backend.set_superseded(ids)`（缺失 id 静默跳过）。
  - `NOOP` → 不做。
  - 返回新写入记录的 id 列表。
  - 注：新记录的 embedding 由 writer 用 embedder 生成（writer 持有 embedder，或经 retriever/backend 复用；见依赖注入）。

### 2.3 set_superseded（sqlite_backend.py）

```python
def set_superseded(self, ids: list[str]) -> None:
    for i in ids:
        row = self._conn.execute("SELECT rowid FROM memory_records WHERE id = ?", (i,)).fetchone()
        if row is None:
            continue
        self._conn.execute("UPDATE memory_records SET superseded = 1 WHERE rowid = ?", (row[0],))
        self._conn.execute("UPDATE memory_vec SET superseded = 1 WHERE rowid = ?", (row[0],))
    self._conn.commit()
```
> vec0 metadata 列 UPDATE **已实测支持**（UPDATE 后 `superseded=0` 检索排除该行、`superseded=1` 检索命中），故直接 UPDATE 两表即可，无需删+重插。backend.py Protocol 加 `set_superseded(ids)` 签名。

### 2.4 集成（config 门控，默认关）

`ConversationMemoryService` 增加可选 `writer: MemoryWriter | None`：
- `record_turn` 内：若 `writer` 存在（config `memory_write_extract=True` 时 assembly 注入）→ `await writer.write(owner_id, kind, text)`；否则走原有 `add_texts` 原文入库（SP1 行为）。
- 采样：config `memory_write_sample_rate`（默认 1.0）可降低触发比例控成本（用确定性哈希采样，避免测试不确定）。
- owner_id/kind：conversation 场景 owner_id=conv_id（沿用现有 collection 映射），kind="conversation"。

### 2.5 配置（app/config.py）
`memory_write_extract: bool = False`、`memory_write_sample_rate: float = 1.0`、`memory_write_candidate_k: int = 5`。assembly 启用时构造 `MemoryWriter(backend, embedder, retriever, completer, ...)` 注入（backend/embedder/retriever 均复用 assembly 已建的同一实例）。

---

## 3. 错误处理与边界

- **LLM 失败 / JSON 非法**：`_extract` → 返回 `[]`（不写）；`_reconcile` → 降级全 ADD（不丢新事实、不误删旧）。全程 try/except，不抛给聊天路径。
- **提炼空**：直接返回，不触发调和（省一次调用）。
- **supersede_ids 缺失**：`set_superseded` 静默跳过不存在的 id。
- **version 计算**：REPLACE 时新记录 `version = max(被 supersede 记录的 version) + 1`；无则 1。
- **应用原子性**：`_apply` 内的 upsert 已是整批预校验（SP1）；set_superseded 单独 commit。一次 write 的多个 op 顺序执行；某 op 失败记日志跳过，不回滚已成功的（记忆写入非事务性关键路径，容忍部分成功）。
- **不阻塞聊天**：写入在轮结束后异步触发（沿用现有 record_turn 的调用时机），失败 best-effort。

---

## 4. 测试策略（TDD，mock LLM completer 返回预设 JSON，不打网络）

1. `test_writer_extract`：mock completer 返回事实 JSON → 得 `ExtractedFact` 列表；返回空/非法 → `[]`。
2. `test_writer_add`：无候选 → ADD → backend 有新记录，mem_type/importance/entity_key 正确。
3. `test_writer_noop_dedup`：调和返回 NOOP → 不新增。
4. `test_writer_replace_by_entity`：同 entity 更新 → 旧 superseded、新 version=旧+1。
5. `test_writer_replace_contradiction`：矛盾 → 旧 superseded、新生效。
6. `test_writer_reconcile_parse_fail_degrades_to_add`：调和 JSON 非法 → 全 ADD、不误删。
7. `test_set_superseded`：置 1 后 SP2 检索默认排除。
8. `test_conversation_memory_default_raw`：`writer=None` → 行为同 SP1（`test_conversation_memory.py` 全绿）。
9. 全仓回归。

---

## 5. 交付顺序（TDD，供 writing-plans 细化）

1. `set_superseded`（backend + Protocol）+ 测试
2. writer 数据类型 + `_extract`（提炼）+ 测试
3. `_gather_candidates` + `_reconcile`（调和）+ 测试
4. `_apply` + `MemoryWriter.write` 端到端（mock LLM）+ 测试
5. `ConversationMemoryService` 集成（config 门控）+ 兼容测试
6. 配置 + assembly 注入 + 全仓回归

---

## 6. 未决/预留
- 提炼/调和 prompt 的具体措辞在实现期打磨；结构化输出用 JSON + 稳健解析（失败安全降级）。
- `memory_write_extract` 默认关，灰度开启；采样率控成本。
- 真实 LLM 质量（提炼准确率、矛盾判定误差）需 SP4 评测扩展量化；SP3 只保证机制正确 + 安全降级。
- vec0 metadata 列 UPDATE 的可行性实现期实测，不支持则用删+重插同 rowid 兜底。

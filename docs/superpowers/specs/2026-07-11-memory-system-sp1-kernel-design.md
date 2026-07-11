# 生产级记忆系统 · SP1 记忆内核 设计规格

- **日期**：2026-07-11
- **状态**：待实现（brainstorming 已定稿）
- **定位**：小规模多用户（几十~几百用户），SQLite 为主、存储/检索抽象成可替换接口、多租户隔离与评测为一等公民
- **前置**：现有 `src/harness/memory/`（③a 记忆 RAG）已在 `main`；分层上下文（L1/L2/L3）已在 `main`

---

## 0. 背景与范围

现有记忆系统是**极简 RAG**：`sqlite-vec` 向量库 + 远程 embedding，仅「写入 → 纯向量检索」。能力边界（已核实）：

- 检索 = 纯向量相似度，无 rerank / 关键词 / recency / hybrid
- collection 隔离靠**查后过滤**（KNN 全表扫，再 Python 里 `coll != collection` 丢弃）→ 扩展性差
- `metadata` 存了但检索不读，无法按 seq/时间/类型下推过滤
- 纯 append，永不更新：无 upsert / 去重 / 矛盾处理
- 无遗忘：无 TTL / 衰减 / consolidation
- 情景记忆 `EpisodeRecorder` 是死代码（未接线）
- embedding 模型版本无跟踪

**「系统性重建」拆成 4 个按依赖排序的子项目**，各自独立走 规格→计划→实现：

| 子项目 | 内容 | 依赖 |
|---|---|---|
| **SP1 记忆内核（本规格）** | 类型化数据模型 + 新存储 schema（过滤下推 + 隔离一等公民）+ `MemoryBackend` 可替换接口 + 向后兼容 + 迁移 | 无 |
| SP2 检索层 | hybrid（向量+FTS5）+ 元数据下推 + recency/importance 加权 + rerank + MMR + 最小评测 harness | SP1 |
| SP3 写入/提炼 | LLM 提炼 + 类型分类 + 重要性打分 + 去重 + upsert-by-entity + 矛盾检测/消解 | SP1 |
| SP4 遗忘/固化 | TTL/衰减 + consolidation（情景→语义）+ 压缩 + 评测扩展 | SP1/SP3 |

**本规格只覆盖 SP1。** SP2/3/4 仅作为「预留扩展点」出现——其字段在 SP1 一次性建好，后续子项目只填值不改 schema。

**存储关键结论（已实测，sqlite-vec 0.1.9）**：vec0 虚拟表原生支持 `partition key`（KNN 只扫本分区 → 租户隔离）与 metadata 列（KNN 时 `WHERE ... AND col=?` **下推过滤**）。故用 vec0 的分区键 + metadata 列把隔离与过滤**下推进 KNN**，彻底取代旧的「查后过滤」；业务数据放一张带索引的 companion 表按 rowid 关联（详见 2.2）。约束：metadata 列**不可为 NULL**，用哨兵值。

---

## 1. 范围与验收

### IN

1. **类型化记忆数据模型** `MemoryRecord`：分型（episodic/semantic/procedural）、隔离（owner_id/kind）、更新（entity_key/version/superseded）、打分（importance/时间戳/access_count）、生命周期（expires_at）、溯源（source/metadata）字段**一次建全**。
2. **新存储表**：vec0 过滤表（owner_id=partition key，kind/mem_type/expires_at/superseded=可下推 metadata 列，embedding）+ companion 数据表 `memory_records`（业务字段 + id/entity 索引），按 rowid 关联。
3. **`MemoryBackend` 可替换接口**（Protocol）+ `SqliteVecBackend` 实现。检索按 `MemoryFilter` 下推过滤。
4. **向后兼容**：`Memory.add_texts/search` 门面保留，改为薄适配器包新 backend；旧 `collection:<user>` → `(owner_id, kind)`。现有消费方零改动。
5. **迁移**：一次性幂等脚本把旧 `memory.db` 行灌入新表。

### OUT（预留扩展点，字段已建但本规格不填/不用）

- 检索质量：rerank / 关键词 / hybrid / recency / importance 加权（→ SP2）
- 写入智能：LLM 提炼 / 打分 / upsert / 矛盾处理（→ SP3，本规格 upsert **仅**提供机械的按 id/entity 覆盖能力，不含矛盾判定）
- 遗忘：TTL 生效 / consolidation（→ SP4，本规格只建 `expires_at` 列，不做过期剔除）
- 换真向量库（pgvector/Qdrant）：本规格只保证接口可切，不实现第二后端

### 验收标准

1. **分区隔离**：owner_id=u1 的写入，用 u2 检索**绝不**召回（vec0 partition key，非查后过滤）。
2. **下推过滤**：`vector_search(filters=MemoryFilter(owner_id=u1, kind="conversation", mem_type="semantic"))` 只返回同时满足三者的记录，过滤发生在 KNN 阶段（SQL WHERE，非 Python 后过滤）。
3. **类型化写入/读取**：`MemoryRecord` 各字段正确落库与回读；哨兵值（entity_key=''、expires_at=0）正确处理。
4. **upsert 机制**：同 id 覆盖；`list_by_entity` 能查回同 (owner_id, kind, entity_key) 的记录（供 SP3 用）。
5. **向后兼容**：现有 `tests/test_memory.py` / `tests/test_store.py` 全绿；`KnowledgeService` / `QuizService` / agent 记忆工具 / `ConversationMemoryService` 无需改动即通过其现有测试。
6. **迁移正确性**：旧 `memory.db` 的行迁移后，语义检索结果与迁移前一致；脚本幂等（重复运行不产生重复行）。
7. 全仓既有测试不回归。

---

## 2. 架构与模块

```
src/harness/memory/
├── record.py          [新增] MemoryRecord / MemType / MemoryFilter / MemoryHit（数据类型）
├── backend.py         [新增] MemoryBackend Protocol + MemoryFilter 语义
├── sqlite_backend.py  [新增] SqliteVecBackend（单张 vec0 表实现）
├── memory.py          [改]   Memory 门面：内部改为包 backend；add_texts/search 保留兼容
├── store.py           [保留/弃用标注] 旧 MemoryStore（迁移期保留，新代码不用）
├── embeddings.py      [不变]
├── chunker.py         [不变]
└── episodic.py        [不变，SP1 不接线；SP3/SP4 再处理]

src/harness/memory/migrate.py   [新增] 旧 memory.db → 新表 一次性幂等迁移
```

### 2.1 数据模型 `MemoryRecord`

```python
class MemType(str, Enum):
    EPISODIC = "episodic"      # 发生过什么（对话、事件）
    SEMANTIC = "semantic"      # 提炼的事实/偏好
    PROCEDURAL = "procedural"  # 学到的做事方法

@dataclass
class MemoryRecord:
    # 身份/隔离
    id: str                    # uuid hex
    owner_id: str              # 租户/用户，partition key
    kind: str                  # 逻辑桶：knowledge / conversation / episode …（替代旧 collection）
    # 分型
    mem_type: MemType
    # 内容
    text: str
    embedding: list[float]      # 写入时必填；检索回读的 MemoryRecord 不回填向量（存于 vec0），为空列表
    # 更新/矛盾（SP3 填；SP1 只建列）
    entity_key: str = ""       # 空=哨兵；非空供 upsert-by-entity
    version: int = 1
    superseded: int = 0        # 0/1，被更新版本取代则置 1
    # 检索打分（SP2 用；SP1 给默认值）
    importance: float = 0.5
    created_at: str = <now>
    last_accessed_at: str = <now>
    access_count: int = 0
    # 生命周期（SP4 用；SP1 只建列）
    expires_at: int = 0        # unix 秒；0=永不过期（哨兵）
    # 溯源
    source: str = ""           # doc_id / conv_id / run_id
    metadata: dict = {}        # 其余自由 JSON
```

### 2.2 存储表（vec0 过滤表 + companion 数据表）

**设计取向**：vec0 表只承载「分区键 + 可下推过滤列 + 向量」，让隔离与过滤在 KNN 内完成；业务数据放一张带二级索引的 companion 表，按 vec0 的 rowid 关联。这和旧的「两表 + 查后过滤」本质不同——**旧设计在 KNN 后用 Python 过滤 collection（错误根源）；本设计过滤已下推进 KNN，companion 表纯粹是数据存储 + 支持 upsert/entity 查询的真索引**。

```sql
-- ① 向量+过滤（vec0）：rowid 自管
CREATE VIRTUAL TABLE memory_vec USING vec0(
  owner_id     TEXT partition key,     -- KNN 只扫本租户（隔离）
  kind         TEXT,                   -- metadata 列，KNN 内下推
  mem_type     TEXT,                   -- metadata 列，KNN 内下推
  superseded   INTEGER,                -- metadata 列（默认只查 0）
  expires_at   INTEGER,                -- metadata 列（SP4 用；SP1 恒 0）
  embedding    float[<dimension>]
);

-- ② 业务数据（普通表）：rowid 与 vec 表对齐，id/entity 建索引供 upsert/召回回填
CREATE TABLE memory_records(
  rowid        INTEGER PRIMARY KEY,    -- = memory_vec.rowid
  id           TEXT UNIQUE NOT NULL,   -- uuid，业务稳定主键
  owner_id     TEXT NOT NULL, kind TEXT NOT NULL, mem_type TEXT NOT NULL,
  text         TEXT NOT NULL, entity_key TEXT NOT NULL DEFAULT '',
  version      INTEGER NOT NULL DEFAULT 1, superseded INTEGER NOT NULL DEFAULT 0,
  importance   REAL NOT NULL DEFAULT 0.5,
  created_at   TEXT NOT NULL, last_accessed_at TEXT NOT NULL,
  access_count INTEGER NOT NULL DEFAULT 0, expires_at INTEGER NOT NULL DEFAULT 0,
  source       TEXT NOT NULL DEFAULT '', metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX ix_mem_id     ON memory_records(id);
CREATE INDEX ix_mem_entity ON memory_records(owner_id, kind, entity_key);
```

- vec0 metadata 列不可空 → `superseded`/`expires_at` 用整数哨兵（0）。
- 写入：先 INSERT `memory_records` 拿 rowid（或先占位），再用**同一 rowid** 写 `memory_vec`（沿用现状 store.py 的 rowid 对齐手法）。
- `vector_search`：KNN 在 `memory_vec` 上带下推过滤 → 得 (rowid, distance) → 按 rowid JOIN `memory_records` 回填 `MemoryRecord`（**过滤已在 KNN 完成，无 Python 后过滤**）。默认追加 `AND superseded = 0`（除非 `include_superseded`）与 `AND (expires_at = 0 OR expires_at > <now>)`（SP4 生效；SP1 恒 0 自然通过）。
- `upsert`/`delete`/`get`/`list_by_entity`：走 `memory_records` 的 id/entity 索引，删除/更新时同步 `memory_vec`（按 rowid）。

### 2.3 存储接口 `MemoryBackend`（可替换切点）

```python
@dataclass
class MemoryFilter:
    owner_id: str
    kind: str | None = None
    mem_type: str | None = None
    include_superseded: bool = False
    include_expired: bool = False

@dataclass
class MemoryHit:
    record: MemoryRecord
    distance: float

class MemoryBackend(Protocol):
    def upsert(self, records: list[MemoryRecord]) -> list[str]: ...      # 按 id 存在则覆盖
    def delete(self, ids: list[str]) -> None: ...
    def get(self, ids: list[str]) -> list[MemoryRecord]: ...
    def vector_search(self, query_embedding: list[float], *,
                      filters: MemoryFilter, k: int) -> list[MemoryHit]: ...
    def keyword_search(self, query_text: str, *,
                       filters: MemoryFilter, k: int) -> list[MemoryHit]: ...   # SP1 最小实现
    def list_by_entity(self, owner_id: str, kind: str,
                       entity_key: str) -> list[MemoryRecord]: ...              # SP3 upsert 用
```

- `SqliteVecBackend(db_path, dimension)` 实现之。`filters` 落成 vec0 KNN 的 `WHERE ... AND` 子句（**下推**），非 Python 后过滤。
- `keyword_search` 在 SP1 **返回空列表 + TODO 注释**（不做半吊子 LIKE），FTS5 真实现留 SP2；签名先定死避免 SP2 改接口。
- 日后 `PgVectorBackend` / `QdrantBackend` 实现同一 Protocol → `Memory` 门面无感知切换。

### 2.4 向后兼容适配

`Memory` 门面保留 `add_texts(texts, collection, metadata)` / `search(query, collection, k)`：

- 旧 `collection` 字符串按约定拆：`"knowledge:<user>"` → `owner_id=<user>, kind="knowledge"`；无 `:` 的（如 `"knowledge"`、`"episodes"`）→ `owner_id="_global", kind=<collection>`。
- `add_texts` 内部：chunk → embed → 构造 `MemoryRecord`（mem_type 默认 semantic、importance 0.5）→ `backend.upsert`。
- `search` 内部：embed query → `backend.vector_search(filters=MemoryFilter(owner_id, kind))` → 回旧 `MemoryHit(text, collection, metadata, distance)` 形状（保持消费方不变）。

> 消费方 `KnowledgeService`（`knowledge:<user>`）、`QuizService`、`ConversationMemoryService`（`conversation:<id>`）、agent 工具全部继续走 `Memory` 门面 → **零改动**。

### 2.5 迁移 `migrate.py`

一次性幂等：读旧 `memory_items` + `memory_vectors`，对每行拆 collection→(owner_id, kind)，mem_type=semantic、importance=0.5、embedding 直接搬（同模型同维），生成 uuid id，`backend.upsert` 进 `memory_v2`。幂等靠迁移标记表 `_memory_migrations(name, done_at)`；已迁移则跳过。旧表保留（回滚安全），确认稳定后另行清理。

---

## 3. 错误处理与边界

- **维度不符**：`upsert` 校验 `len(embedding)==dimension`，不符抛 `ValueError`（沿用现状语义）。
- **metadata 列 NULL**：构造 SQL 前把 None → 哨兵，杜绝 `Expected integer ... received NULL`。
- **owner_id 缺省**：兼容路径无 user 的旧 collection → `owner_id="_global"`，保证不串租户。
- **迁移中断**：迁移逐行 upsert + 末尾写标记；中断后重跑，因 upsert 按 id 覆盖 + 标记表，幂等安全。
- **embedding 端点故障**：属 embeddings.py 现状范畴，SP1 不改。

---

## 4. 测试策略

用现有 `mock_embedder` fixture，不打网络：

1. `test_memory_record` — 字段默认值、哨兵、序列化往返。
2. `test_sqlite_backend`：
   - 分区隔离：u1 写、u2 查空。
   - 下推过滤：kind / mem_type 过滤在 SQL 阶段生效（构造跨 kind/type 数据验证）。
   - upsert 覆盖（同 id）、delete、get、list_by_entity。
   - superseded 默认排除。
3. `test_memory_facade_compat` — 旧 `add_texts/search` 语义不变（复用/对齐现有 `test_memory.py` 断言）。
4. `test_migrate` — 造旧库 → 迁移 → 检索一致 + 幂等（重复迁移无重复）。
5. 回归：`tests/test_memory.py`、`tests/test_store.py`、`tests/app/test_knowledge.py`、`tests/app/test_conversation_memory.py`、quiz 相关全绿。

---

## 5. 交付顺序（TDD，供 writing-plans 细化）

1. `record.py`（数据类型）+ 单测
2. `backend.py`（Protocol/Filter/Hit）
3. `sqlite_backend.py`（vec0 单表 + 下推）+ 单测（隔离/过滤/upsert）
4. `memory.py` 适配器改造 + 兼容测试（旧测试保持绿）
5. `migrate.py` + 迁移测试
6. 全仓回归

---

## 6. 未决/预留

- `keyword_search` SP1 仅占位，FTS5 到 SP2；届时**不改**接口签名。
- `importance` 默认 0.5，真实打分到 SP3。
- `expires_at` 恒 0，TTL 剔除到 SP4。
- embedding 模型版本化（换模型再嵌入迁移）列入 SP4/运维，SP1 不做。

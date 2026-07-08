# AI Harness 记忆/RAG（子项目③a）设计规格

- **日期**：2026-07-08
- **状态**：待实现（brainstorming 已定稿）
- **定位**：单用户自用工具，在①内核 + ②可靠性层之上扩展
- **前置**：①（Agent Loop/模型抽象/工具系统/上下文/事件流）、②（OTel 可观测性/重试自纠正/资源上限）已完成并在 `main`

---

## 0. 背景与范围

子项目③（能力扩展）拆成多个独立子项目：③a 记忆/RAG（本规格）、③b 沙箱执行+真实工具、③c 多 Agent 编排、③d 持久化（checkpoint + SQLite 全轨迹）。本规格只覆盖 **③a 长期记忆 RAG**。

**存储栈**：SQLite + **sqlite-vec**（向量存储与 KNN）。可观测性沿用 OpenTelemetry（工具执行自动进②的 `tool_call` span）。

设计通则：严格 YAGNI；embedding/存储可 mock、不打网络即可测；记忆经**普通工具**接入，不改 loop / ContextManager；沿用①②的客户端抽象与工具模式。

---

## 1. 范围与验收

### IN
给定纯文本的长期记忆 RAG 闭环：
```
add_texts(文本, collection) → 分块 → embedding → 存 sqlite-vec
search(query, collection, k) → embed query → 向量 KNN top-k → 回填
```
对外提供：`EmbeddingClient`（兼容端点、独立配置）、`MemoryStore`（sqlite-vec、collection 感知）、`Memory` 门面（ingest API）、两个工具 `search_memory`（读）/ `remember`（写）。

### OUT（预留扩展点）
文件格式解析（PDF/docx/HTML → App 层）、情景记忆、`ContextManager` 自动注入、rerank / 跨文档去重、本地 embedding 模型。

### 验收标准
1. `memory.add_texts([...], collection="knowledge")` 后，`search` 语义相近 query 能召回对应文本并按相似度排序。
2. 长文本按 `chunk_size`+`overlap` 分块后分别入库。
3. `search_memory` 工具在 agent loop 中被调用 → 检索结果回填 → 模型据此作答（mock 模型 + mock embedder，不打网络）。
4. `remember` 工具写入后可被 `search_memory` 召回。
5. collection 隔离：写入 `knowledge` 不会在查 `notes` 时被召回。
6. ①②全部原有测试不回归。

---

## 2. 架构与模块

**设计取向**：新增 `memory/` 子系统产出可复用向量基础设施（供未来情景记忆共用）；记忆能力经**两个普通工具**接入 loop——自动走②已有的工具 span / 事件 / 错误回填，无需改 loop / ContextManager。embedding 仿①的 `ModelClient` 做成 `EmbeddingClient` 协议。

```
src/harness/memory/            [新增]
├── __init__.py
├── embeddings.py    EmbeddingClient 协议 + OpenAICompatibleEmbeddingClient
├── chunker.py       chunk(text, size, overlap) → list[str]（纯函数）
├── store.py         MemoryStore（sqlite-vec 建表/写入/KNN）+ MemoryHit
└── memory.py        Memory 门面：串起 chunker+embedder+store
src/harness/tools/builtins/    [新增]
├── memory_search.py SearchMemoryTool(memory)
└── memory_write.py  RememberTool(memory)
src/harness/config.py          [改] embedding 端点 + 记忆库路径 + 分块/检索参数
```

**依赖新增**：`sqlite-vec`。embedding 复用 `openai` async client（指向 embedding 端点）。

**边界**：
- `EmbeddingClient` 是协议——测试用确定性 mock，不打网络。
- `MemoryStore` 只认向量+文本+collection，不认 LLM/工具，`:memory:` 库可单测。
- `Memory` 门面是唯一"知道 chunker/embedder/store 三者"的单元。
- 两个工具持有 `Memory` 引用，其余与①`CalculatorTool` 同构。

---

## 3. EmbeddingClient

```python
class EmbeddingClient(Protocol):
    dimension: int
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
```

v1 实现 `OpenAICompatibleEmbeddingClient`：`openai` async client 调 `/embeddings`，**端点独立配置**（`embedding_base_url`/`embedding_api_key`/`embedding_model`/`embedding_dimension`），可与聊天端点不同。批量 embed（一次请求多条）。

- 职责边界：只把 `list[str]` → `list[vector]`，不碰存储/检索。
- `dimension` 必须与 `MemoryStore` 建表维度一致；不一致应在装配时报错。
- 测试用 `MockEmbeddingClient`（确定性向量），不打网络。

---

## 4. MemoryStore（sqlite-vec）

**Schema**（加载 sqlite-vec 扩展）：
```sql
CREATE VIRTUAL TABLE memory_vectors USING vec0(embedding float[DIM]);
CREATE TABLE memory_items(
  id INTEGER PRIMARY KEY,        -- = 向量表 rowid
  collection TEXT NOT NULL,
  text TEXT NOT NULL,
  metadata TEXT,                 -- JSON
  created_at TEXT NOT NULL
);
```

**接口**：
```python
class MemoryHit:  text: str; collection: str; metadata: dict; distance: float
class MemoryStore:
    def __init__(self, db_path: str, dimension: int): ...   # 加载扩展 + 建表
    def add(self, items: list[tuple[str, str, dict, list[float]]]) -> list[int]  # (collection,text,metadata,embedding)
    def search(self, collection: str, query_embedding: list[float], k: int) -> list[MemoryHit]
    def delete(self, ids: list[int]) -> None
```

**检索**：sqlite-vec KNN `WHERE embedding MATCH ? AND k = ?` 取近邻，JOIN `memory_items` 拿文本，按 collection 过滤。v1 用 **over-fetch（取 k×4）后按 collection 过滤取前 k** 的稳妥做法（兼容不同 sqlite-vec 版本）；partition-key 优化留待需要。单用户数据量足够。

**边界**：纯存储/检索，同步 sqlite 操作（单用户可接受），不认 LLM。`:memory:` 库可单测。
**注意**：需 Python `sqlite3` 支持 `enable_load_extension`；若被禁用改用 `pysqlite3-binary`（实现时确认）。

---

## 5. Chunker + Memory 门面

**Chunker**（纯函数）：`chunk(text, chunk_size, overlap) -> list[str]`，按字符定长切分、相邻块重叠 `overlap`；短于 `chunk_size` 不切。

**Memory 门面**：
```python
class Memory:
    def __init__(self, store: MemoryStore, embedder: EmbeddingClient,
                 chunk_size: int, overlap: int): ...
    async def add_texts(self, texts: list[str], collection: str,
                        metadata: dict | None = None) -> list[int]:
        # 每条 text → chunk() → embedder.embed(所有块) → store.add()
    async def search(self, query: str, collection: str, k: int) -> list[MemoryHit]:
        # embedder.embed([query]) → store.search()
```

**数据流**：
- 摄入（App/工具）：`add_texts(["…"], "knowledge", {"source":"doc1"})` → 分块 → 批量 embed → 入库。
- 检索（agent 读）：模型发 `search_memory({query,k})` → `memory.search` → 命中回填 → 模型作答。
- 写入（agent 写）：模型发 `remember({text})` → `memory.add_texts` 到指定 collection。

---

## 6. 工具（search_memory / remember）

与①`CalculatorTool` 同构，构造时持有 `Memory` 引用：

```python
class SearchMemoryTool(Tool):
    name = "search_memory"
    description = "在长期记忆/知识库中检索与查询相关的内容。"
    class Params(BaseModel):
        query: str
        k: int = 5
    def __init__(self, memory: Memory, collection: str = "knowledge"): ...
    async def run(self, p) -> str:
        hits = await self._memory.search(p.query, self._collection, p.k)
        return 格式化(hits)   # 编号+文本+source；无命中返回明确提示

class RememberTool(Tool):
    name = "remember"
    description = "把一段值得长期记住的信息写入知识库。"
    class Params(BaseModel):
        text: str
    def __init__(self, memory: Memory, collection: str = "knowledge"): ...
    async def run(self, p) -> str:
        ids = await self._memory.add_texts([p.text], self._collection)
        return f"已记住（{len(ids)} 块）。"
```

- 自动继承②：工具执行进 `tool_call` span、错误回填自纠正、结果截断。
- 注册：`registry.register(SearchMemoryTool(memory)); registry.register(RememberTool(memory))`。

---

## 7. 配置 / 测试 / 依赖

### 新增配置（`config.py`，安全默认）
```
embedding_base_url: str = "https://api.openai.com/v1"
embedding_api_key: str = ""            # 空则回退用 api_key
embedding_model: str = "text-embedding-3-small"
embedding_dimension: int = 1536
memory_db_path: str = "memory.db"      # 测试用 ":memory:"
chunk_size: int = 1000
chunk_overlap: int = 200
search_top_k: int = 5
memory_collection: str = "knowledge"
```

### 测试策略（mock 优先、不打网络）
- `MockEmbeddingClient`：文本确定性映射到固定小维度向量（可断言近邻）。
- `Chunker` 单测（重叠、边界、短文本不切）。
- `MemoryStore` 用 `:memory:` + 真实 sqlite-vec：写入 → 按距离返回最近邻 → collection 过滤。
- `Memory` 门面：`add_texts` 分块+embed+入库（断言块数）；`search` 召回相关文本。
- 工具：`RememberTool` 写 → `SearchMemoryTool` 召回 往返；经 `ToolExecutor`。
- 集成：`AgentLoop` + mock 模型（发 `search_memory`）+ mock embedder + `:memory:` store → 断言回填并被作答引用。
- collection 隔离测试。

### 依赖新增
`sqlite-vec`。（embedding 复用 `openai`。）

---

## 8. 后续衔接（备忘，非本次范围）

- **③a-follow 情景记忆**：复用 `MemoryStore`/`EmbeddingClient`，新增 collection（如 `episodes`）+ "何时写一次任务经验、检索时怎么判相关"的逻辑。
- **自动注入**：`ContextManager.build()` 挂 `Retriever`，每轮按用户消息注入 top-k（①预留的扩展点）。
- **③b/③c/③d**：沙箱真实工具、多 Agent 编排、持久化。
- App 层：文件格式解析（PDF/docx→文本）适配器 → 调 `Memory.add_texts`。

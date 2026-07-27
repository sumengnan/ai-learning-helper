> ⚠️ **历史设计记录（已过时）**：harness 内核已抽成外部包
> [ai-harness-framework](https://github.com/sumengnan/ai-harness-framework)（import 名仍是 `harness`）。
> 本文是带日期的设计存档，文中的 `src/harness/` 路径与打包配置反映**当时**的仓库结构、未随抽包更新；
> 当前结构以 [架构文档](../../architecture-harness.md) 为准。

# AI Harness 记忆/RAG（子项目③a）实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 长期记忆 RAG 闭环——文本分块 → embedding → 存 sqlite-vec → KNN 检索 → 经 `search_memory`/`remember` 两个工具接入 agent loop。

**架构：** 新增 `memory/`（`EmbeddingClient` 协议 + sqlite-vec `MemoryStore` + `chunker` + `Memory` 门面），两个工具持有 `Memory` 引用注册进 `ToolRegistry`。不改 loop/ContextManager。

**技术栈：** 沿用①② · 新增 `sqlite-vec`。embedding 复用 `openai`(async)。

**规格：** `docs/superpowers/specs/2026-07-08-harness-memory-rag-design.md`

**提交规范：** git 身份已是 sumengnan，默认提交；**任何 commit message 不得出现 Claude/AI/Co-Authored-By 等署名**。

---

## 文件结构与职责

| 文件 | 动作 | 职责 |
|---|---|---|
| `src/harness/memory/chunker.py` | 新增 | `chunk(text, size, overlap)` 纯函数 |
| `src/harness/memory/embeddings.py` | 新增 | `EmbeddingClient` 协议 + `OpenAICompatibleEmbeddingClient` |
| `src/harness/memory/store.py` | 新增 | `MemoryStore`（sqlite-vec）+ `MemoryHit` |
| `src/harness/memory/memory.py` | 新增 | `Memory` 门面（chunk+embed+store） |
| `src/harness/tools/builtins/memory_search.py` | 新增 | `SearchMemoryTool` |
| `src/harness/tools/builtins/memory_write.py` | 新增 | `RememberTool` |
| `src/harness/config.py` | 改 | embedding 端点 + 记忆库路径 + 分块/检索参数 |
| `tests/conftest.py` | 改 | `MockEmbeddingClient`（确定性向量） |
| `pyproject.toml` | 改 | 新增 `sqlite-vec` |

---

## 任务 0：依赖与配置

**文件：** 改 `pyproject.toml`、`src/harness/config.py`、测试 `tests/test_config.py`

- [ ] **步骤 1：`pyproject.toml` 的 `dependencies` 追加**

```toml
    "sqlite-vec>=0.1.6",
```

- [ ] **步骤 2：`uv sync`**

运行：`uv sync`　预期：装上 sqlite-vec。

- [ ] **步骤 3：写失败测试**（`tests/test_config.py` 追加）

```python
def test_memory_defaults():
    cfg = HarnessConfig(api_key="k")
    assert cfg.embedding_base_url.endswith("/v1")
    assert cfg.embedding_model == "text-embedding-3-small"
    assert cfg.embedding_dimension == 1536
    assert cfg.memory_db_path == "memory.db"
    assert cfg.chunk_size == 1000
    assert cfg.chunk_overlap == 200
    assert cfg.search_top_k == 5
    assert cfg.memory_collection == "knowledge"
```

运行：预期 FAIL。

- [ ] **步骤 4：改 `src/harness/config.py`**，在末尾字段后追加：

```python
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str = ""          # 空则回退用 api_key
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 1536
    memory_db_path: str = "memory.db"
    chunk_size: int = 1000
    chunk_overlap: int = 200
    search_top_k: int = 5
    memory_collection: str = "knowledge"
```

- [ ] **步骤 5：跑通并 commit**

运行：`uv run pytest tests/test_config.py -v`　预期全 pass。
```bash
git add pyproject.toml uv.lock src/harness/config.py tests/test_config.py
git commit -m "chore: 记忆/RAG 依赖与配置项"
```

---

## 任务 1：分块 `memory/chunker.py`

**文件：** 创建 `src/harness/memory/__init__.py`、`src/harness/memory/chunker.py`、测试 `tests/test_chunker.py`

- [ ] **步骤 1：建包**　运行：`touch src/harness/memory/__init__.py`

- [ ] **步骤 2：写失败测试**

```python
# tests/test_chunker.py
import pytest
from harness.memory.chunker import chunk


def test_short_text_not_split():
    assert chunk("hi", 1000, 200) == ["hi"]


def test_empty_text():
    assert chunk("   ", 1000, 200) == []


def test_long_text_split_with_overlap():
    text = "".join(str(i % 10) for i in range(2500))  # 2500 字符
    chunks = chunk(text, 1000, 200)
    assert len(chunks) == 4
    assert chunks[0] == text[0:1000]
    assert chunks[1] == text[800:1800]         # 步长 800，与前块重叠 200
    assert chunks[1][:200] == chunks[0][-200:]  # 重叠内容一致


def test_invalid_params():
    with pytest.raises(ValueError):
        chunk("x", 0, 0)
    with pytest.raises(ValueError):
        chunk("x", 100, 100)
```

运行：预期 FAIL。

- [ ] **步骤 3：实现 `src/harness/memory/chunker.py`**

```python
# src/harness/memory/chunker.py
from __future__ import annotations


def chunk(text: str, chunk_size: int, overlap: int) -> list[str]:
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须为正")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap 必须在 [0, chunk_size) 内")
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    step = chunk_size - overlap
    chunks: list[str] = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + chunk_size])
        start += step
    return chunks
```

- [ ] **步骤 4：跑通并 commit**

运行：`uv run pytest tests/test_chunker.py -v`　预期：4 passed。
```bash
git add src/harness/memory/__init__.py src/harness/memory/chunker.py tests/test_chunker.py
git commit -m "feat: memory chunker 文本分块"
```

---

## 任务 2：EmbeddingClient + MockEmbeddingClient

**文件：** 创建 `src/harness/memory/embeddings.py`、改 `tests/conftest.py`、测试 `tests/test_embeddings.py`

- [ ] **步骤 1：`tests/conftest.py` 追加 `MockEmbeddingClient` 与 fixture**

```python
import hashlib
import math


class MockEmbeddingClient:
    """确定性 embedder：按空白分词哈希到固定维度并归一化。
    共享词的文本向量更接近，便于断言近邻。不打网络。
    """

    def __init__(self, dimension: int = 64):
        self.dimension = dimension

    async def embed(self, texts):
        return [self._vec(t) for t in texts]

    def _vec(self, text: str):
        v = [0.0] * self.dimension
        for token in text.lower().split():
            h = int(hashlib.md5(token.encode()).hexdigest(), 16)
            v[h % self.dimension] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]


@pytest.fixture
def mock_embedder():
    return MockEmbeddingClient
```

- [ ] **步骤 2：写失败测试**

```python
# tests/test_embeddings.py
import pytest

from harness.memory.embeddings import OpenAICompatibleEmbeddingClient, EmbeddingClient


async def test_mock_embedder_is_deterministic_and_shaped(mock_embedder):
    emb = mock_embedder(dimension=64)
    a = await emb.embed(["cat dog"])
    b = await emb.embed(["cat dog"])
    assert a == b
    assert len(a[0]) == 64


async def test_openai_embedding_client_calls_api(monkeypatch):
    client = OpenAICompatibleEmbeddingClient(
        base_url="http://x/v1", api_key="k", model="m", dimension=3)

    class _D:
        def __init__(self, e): self.embedding = e

    class _Resp:
        data = [_D([1.0, 2.0, 3.0]), _D([4.0, 5.0, 6.0])]

    async def fake_create(model, input):
        assert model == "m"
        assert input == ["a", "b"]
        return _Resp()

    monkeypatch.setattr(client._client.embeddings, "create", fake_create)
    out = await client.embed(["a", "b"])
    assert out == [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
    assert client.dimension == 3
```

> 注：所有 async 测试直接用 `async def`（项目 `pyproject.toml` 已配 `asyncio_mode=auto`）。

运行：预期 FAIL。

- [ ] **步骤 3：实现 `src/harness/memory/embeddings.py`**

```python
# src/harness/memory/embeddings.py
from __future__ import annotations

from typing import Protocol, runtime_checkable

from openai import AsyncOpenAI


@runtime_checkable
class EmbeddingClient(Protocol):
    dimension: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class OpenAICompatibleEmbeddingClient:
    """调用 OpenAI 兼容 /embeddings 端点。端点与聊天端点独立配置。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        dimension: int,
        timeout: float = 60.0,
    ) -> None:
        self.dimension = dimension
        self._model = model
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        resp = await self._client.embeddings.create(model=self._model, input=texts)
        return [d.embedding for d in resp.data]
```

- [ ] **步骤 4：跑通并 commit**

运行：`uv run pytest tests/test_embeddings.py -v`　预期：2 passed。
```bash
git add src/harness/memory/embeddings.py tests/conftest.py tests/test_embeddings.py
git commit -m "feat: EmbeddingClient 协议与兼容端点实现 + Mock"
```

---

## 任务 3：向量存储 `memory/store.py`

**文件：** 创建 `src/harness/memory/store.py`、测试 `tests/test_store.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/test_store.py
import pytest

from harness.memory.store import MemoryStore, MemoryHit


def _store():
    return MemoryStore(":memory:", dimension=4)


def test_add_and_search_returns_nearest():
    s = _store()
    s.add([
        ("knowledge", "a", {"source": "s1"}, [1.0, 0.0, 0.0, 0.0]),
        ("knowledge", "b", {}, [0.0, 1.0, 0.0, 0.0]),
        ("knowledge", "c", {}, [0.0, 0.0, 1.0, 0.0]),
    ])
    hits = s.search("knowledge", [1.0, 0.0, 0.0, 0.0], k=2)
    assert hits[0].text == "a"                 # 最近
    assert hits[0].metadata["source"] == "s1"
    assert len(hits) == 2
    assert isinstance(hits[0], MemoryHit)


def test_collection_isolation():
    s = _store()
    s.add([
        ("knowledge", "k1", {}, [1.0, 0.0, 0.0, 0.0]),
        ("notes", "n1", {}, [1.0, 0.0, 0.0, 0.0]),
    ])
    hits = s.search("notes", [1.0, 0.0, 0.0, 0.0], k=5)
    assert [h.text for h in hits] == ["n1"]    # 不串 collection


def test_delete():
    s = _store()
    ids = s.add([("knowledge", "x", {}, [1.0, 0.0, 0.0, 0.0])])
    s.delete(ids)
    assert s.search("knowledge", [1.0, 0.0, 0.0, 0.0], k=5) == []
```

运行：预期 FAIL。

- [ ] **步骤 2：实现 `src/harness/memory/store.py`**

```python
# src/harness/memory/store.py
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

import sqlite_vec


@dataclass
class MemoryHit:
    text: str
    collection: str
    metadata: dict
    distance: float


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    """sqlite-vec 向量存储。向量表 rowid 与元数据表 id 对齐。"""

    def __init__(self, db_path: str, dimension: int) -> None:
        self._dim = dimension
        self._conn = sqlite3.connect(db_path)
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)
        self._conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS memory_vectors "
            f"USING vec0(embedding float[{dimension}])"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS memory_items("
            "id INTEGER PRIMARY KEY, collection TEXT NOT NULL, text TEXT NOT NULL, "
            "metadata TEXT, created_at TEXT NOT NULL)"
        )
        self._conn.commit()

    def add(self, items: list[tuple[str, str, dict, list[float]]]) -> list[int]:
        ids: list[int] = []
        for collection, text, metadata, embedding in items:
            cur = self._conn.execute(
                "INSERT INTO memory_items(collection, text, metadata, created_at) "
                "VALUES (?, ?, ?, ?)",
                (collection, text, json.dumps(metadata or {}), _now()),
            )
            rowid = cur.lastrowid
            self._conn.execute(
                "INSERT INTO memory_vectors(rowid, embedding) VALUES (?, ?)",
                (rowid, sqlite_vec.serialize_float32(embedding)),
            )
            ids.append(rowid)
        self._conn.commit()
        return ids

    def search(self, collection: str, query_embedding: list[float], k: int) -> list[MemoryHit]:
        over = k * 4  # over-fetch 后按 collection 过滤
        rows = self._conn.execute(
            "SELECT rowid, distance FROM memory_vectors "
            "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (sqlite_vec.serialize_float32(query_embedding), over),
        ).fetchall()
        hits: list[MemoryHit] = []
        for rowid, distance in rows:
            item = self._conn.execute(
                "SELECT collection, text, metadata FROM memory_items WHERE id = ?",
                (rowid,),
            ).fetchone()
            if item is None:
                continue
            coll, text, metadata = item
            if coll != collection:
                continue
            hits.append(MemoryHit(
                text=text, collection=coll,
                metadata=json.loads(metadata or "{}"), distance=distance))
            if len(hits) >= k:
                break
        return hits

    def delete(self, ids: list[int]) -> None:
        for i in ids:
            self._conn.execute("DELETE FROM memory_items WHERE id = ?", (i,))
            self._conn.execute("DELETE FROM memory_vectors WHERE rowid = ?", (i,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/test_store.py -v`　预期：3 passed。
> 若报 `enable_load_extension` 不可用（个别 Python 构建禁用了扩展加载），改依赖为 `pysqlite3-binary` 并 `import pysqlite3 as sqlite3`；先确认 uv 装的 Python 是否支持，多数支持。
```bash
git add src/harness/memory/store.py tests/test_store.py
git commit -m "feat: MemoryStore sqlite-vec 向量存储与 KNN 检索"
```

---

## 任务 4：记忆门面 `memory/memory.py`

**文件：** 创建 `src/harness/memory/memory.py`、测试 `tests/test_memory.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/test_memory.py
from harness.memory.memory import Memory
from harness.memory.store import MemoryStore


async def test_add_texts_chunks_and_stores(mock_embedder):
    store = MemoryStore(":memory:", dimension=64)
    mem = Memory(store, mock_embedder(dimension=64), chunk_size=100, overlap=20)
    long_text = "词 " * 200                       # 远超 100 字符 → 多块
    ids = await mem.add_texts([long_text], "knowledge", {"source": "doc1"})
    assert len(ids) >= 2                           # 分成多块入库


async def test_search_recalls_relevant(mock_embedder):
    store = MemoryStore(":memory:", dimension=64)
    mem = Memory(store, mock_embedder(dimension=64), chunk_size=1000, overlap=0)
    await mem.add_texts(["python programming language"], "knowledge")
    await mem.add_texts(["the cat sat on the mat"], "knowledge")
    hits = await mem.search("cat", "knowledge", k=1)
    assert hits[0].text == "the cat sat on the mat"   # 共享 "cat" → 更近


async def test_add_empty_text_returns_empty(mock_embedder):
    store = MemoryStore(":memory:", dimension=64)
    mem = Memory(store, mock_embedder(dimension=64), chunk_size=100, overlap=20)
    assert await mem.add_texts(["   "], "knowledge") == []
```

运行：预期 FAIL。

- [ ] **步骤 2：实现 `src/harness/memory/memory.py`**

```python
# src/harness/memory/memory.py
from __future__ import annotations

from .chunker import chunk
from .embeddings import EmbeddingClient
from .store import MemoryHit, MemoryStore


class Memory:
    """串起 chunker + embedder + store 的门面。唯一同时知道三者的单元。"""

    def __init__(
        self,
        store: MemoryStore,
        embedder: EmbeddingClient,
        chunk_size: int = 1000,
        overlap: int = 200,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._chunk_size = chunk_size
        self._overlap = overlap

    async def add_texts(
        self, texts: list[str], collection: str, metadata: dict | None = None
    ) -> list[int]:
        all_chunks: list[str] = []
        for t in texts:
            all_chunks.extend(chunk(t, self._chunk_size, self._overlap))
        if not all_chunks:
            return []
        vectors = await self._embedder.embed(all_chunks)
        items = [(collection, c, metadata, v) for c, v in zip(all_chunks, vectors)]
        return self._store.add(items)

    async def search(self, query: str, collection: str, k: int) -> list[MemoryHit]:
        vectors = await self._embedder.embed([query])
        return self._store.search(collection, vectors[0], k)
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/test_memory.py -v`　预期：3 passed。
```bash
git add src/harness/memory/memory.py tests/test_memory.py
git commit -m "feat: Memory 门面（分块+embed+存取）"
```

---

## 任务 5：工具 search_memory / remember

**文件：** 创建 `src/harness/tools/builtins/memory_search.py`、`src/harness/tools/builtins/memory_write.py`、测试 `tests/test_memory_tools.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/test_memory_tools.py
from harness.memory.memory import Memory
from harness.memory.store import MemoryStore
from harness.tools.base import ToolRegistry, ToolExecutor
from harness.tools.builtins.memory_search import SearchMemoryTool
from harness.tools.builtins.memory_write import RememberTool
from harness.types import ToolCall


def _mem(mock_embedder):
    return Memory(MemoryStore(":memory:", dimension=64),
                  mock_embedder(dimension=64), chunk_size=1000, overlap=0)


async def test_remember_then_search_roundtrip(mock_embedder):
    mem = _mem(mock_embedder)
    reg = ToolRegistry()
    reg.register(RememberTool(mem))
    reg.register(SearchMemoryTool(mem))
    ex = ToolExecutor(reg)

    w = await ex.execute(ToolCall(id="c1", name="remember",
                                  arguments={"text": "the cat sat on the mat"}))
    assert w.is_error is False
    assert "已记住" in w.content

    r = await ex.execute(ToolCall(id="c2", name="search_memory",
                                  arguments={"query": "cat", "k": 3}))
    assert r.is_error is False
    assert "the cat sat on the mat" in r.content


async def test_search_no_hits_message(mock_embedder):
    mem = _mem(mock_embedder)
    reg = ToolRegistry()
    reg.register(SearchMemoryTool(mem))
    ex = ToolExecutor(reg)
    r = await ex.execute(ToolCall(id="c1", name="search_memory",
                                  arguments={"query": "anything", "k": 3}))
    assert "未在知识库" in r.content
```

运行：预期 FAIL。

- [ ] **步骤 2：实现两个工具**

```python
# src/harness/tools/builtins/memory_search.py
from __future__ import annotations

from pydantic import BaseModel

from ..base import Tool
from ...memory.memory import Memory


class SearchMemoryTool(Tool):
    name = "search_memory"
    description = "在长期记忆/知识库中检索与查询相关的内容，返回最相关的若干条文本。"

    class Params(BaseModel):
        query: str
        k: int = 5

    def __init__(self, memory: Memory, collection: str = "knowledge") -> None:
        self._memory = memory
        self._collection = collection

    async def run(self, params: "SearchMemoryTool.Params") -> str:
        hits = await self._memory.search(params.query, self._collection, params.k)
        if not hits:
            return "（未在知识库中检索到相关内容）"
        lines = []
        for i, h in enumerate(hits, 1):
            src = h.metadata.get("source")
            tag = f"（来源：{src}）" if src else ""
            lines.append(f"[{i}]{tag} {h.text}")
        return "\n".join(lines)
```

```python
# src/harness/tools/builtins/memory_write.py
from __future__ import annotations

from pydantic import BaseModel

from ..base import Tool
from ...memory.memory import Memory


class RememberTool(Tool):
    name = "remember"
    description = "把一段值得长期记住的信息写入知识库，供以后检索。"

    class Params(BaseModel):
        text: str

    def __init__(self, memory: Memory, collection: str = "knowledge") -> None:
        self._memory = memory
        self._collection = collection

    async def run(self, params: "RememberTool.Params") -> str:
        ids = await self._memory.add_texts([params.text], self._collection)
        return f"已记住（{len(ids)} 块）。"
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/test_memory_tools.py -v`　预期：2 passed。
```bash
git add src/harness/tools/builtins/memory_search.py src/harness/tools/builtins/memory_write.py tests/test_memory_tools.py
git commit -m "feat: search_memory / remember 记忆工具"
```

---

## 任务 6：端到端集成测试 + demo 组装

**文件：** 测试 `tests/test_memory_integration.py`、新增 `examples/memory_demo.py`

- [ ] **步骤 1：写集成测试**（agent loop 中调用 search_memory，mock 模型 + mock embedder + :memory: store）

```python
# tests/test_memory_integration.py
from harness.loop.agent_loop import AgentLoop
from harness.context.manager import ContextManager
from harness.tools.base import ToolRegistry
from harness.tools.builtins.memory_search import SearchMemoryTool
from harness.memory.memory import Memory
from harness.memory.store import MemoryStore
from harness.llm.base import StreamChunk, ToolCallDelta
from harness.events import ToolFinished, RunFinished


async def test_agent_uses_search_memory(make_mock, text_turn, mock_embedder):
    mem = Memory(MemoryStore(":memory:", dimension=64),
                 mock_embedder(dimension=64), chunk_size=1000, overlap=0)
    await mem.add_texts(["光合作用把二氧化碳和水转化为葡萄糖和氧气"], "knowledge",
                        {"source": "生物笔记"})

    reg = ToolRegistry()
    reg.register(SearchMemoryTool(mem))
    ctx = ContextManager(system_prompt="s")

    search_turn = [
        StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
            index=0, id="c1", name="search_memory",
            arguments='{"query": "光合作用", "k": 3}')),
        StreamChunk(type="done"),
    ]
    loop = AgentLoop(client=make_mock([search_turn, text_turn("据知识库，答案如上")]),
                     registry=reg, context=ctx, max_steps=5,
                     run_id_factory=lambda: "r1")
    events = [e async for e in loop.run("什么是光合作用？")]

    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert "光合作用" in finished[0].result.content        # 检索结果回填
    assert finished[0].result.is_error is False
    assert isinstance(events[-1], RunFinished)
```

运行：`uv run pytest tests/test_memory_integration.py -v`　预期：1 passed。

- [ ] **步骤 2：新增 `examples/memory_demo.py`**（组装接线，供手动验收；需真实 embedding key）

```python
# examples/memory_demo.py
"""记忆/RAG 手动验收：先写入知识库，再让 agent 检索作答。
需要 .env 配好聊天端点与 embedding 端点（HARNESS_EMBEDDING_* / HARNESS_API_KEY）。

运行：uv run python examples/memory_demo.py
"""
from __future__ import annotations

import asyncio

from harness.config import HarnessConfig
from harness.context.manager import ContextManager
from harness.llm.openai_compat import OpenAICompatibleClient
from harness.loop.agent_loop import AgentLoop
from harness.memory.embeddings import OpenAICompatibleEmbeddingClient
from harness.memory.memory import Memory
from harness.memory.store import MemoryStore
from harness.tools.base import ToolRegistry
from harness.tools.builtins.memory_search import SearchMemoryTool
from harness.tools.builtins.memory_write import RememberTool
from harness.events import TextDelta, ToolStarted, ToolFinished, RunFinished


async def main() -> None:
    cfg = HarnessConfig()
    embedder = OpenAICompatibleEmbeddingClient(
        base_url=cfg.embedding_base_url,
        api_key=cfg.embedding_api_key or cfg.api_key,
        model=cfg.embedding_model,
        dimension=cfg.embedding_dimension,
    )
    store = MemoryStore(cfg.memory_db_path, cfg.embedding_dimension)
    memory = Memory(store, embedder, cfg.chunk_size, cfg.chunk_overlap)

    await memory.add_texts(
        ["光合作用是植物利用光能把二氧化碳和水转化为葡萄糖和氧气的过程。"],
        cfg.memory_collection, {"source": "生物笔记"})

    registry = ToolRegistry()
    registry.register(SearchMemoryTool(memory, cfg.memory_collection))
    registry.register(RememberTool(memory, cfg.memory_collection))

    loop = AgentLoop(
        client=OpenAICompatibleClient(cfg),
        registry=registry,
        context=ContextManager(system_prompt="你可以用 search_memory 查询知识库来回答问题。"),
        max_steps=cfg.max_steps,
        model_name=cfg.model,
    )

    async for ev in loop.run("根据知识库，什么是光合作用？"):
        if isinstance(ev, TextDelta):
            print(ev.text, end="", flush=True)
        elif isinstance(ev, ToolStarted):
            print(f"\n[检索] {ev.tool_call.arguments}")
        elif isinstance(ev, ToolFinished):
            print(f"[命中] {ev.result.content[:120]}")
        elif isinstance(ev, RunFinished):
            print(f"\n\n[完成] {ev.message.content}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **步骤 3：全量测试并 commit**

运行：`uv run pytest -q`　预期：全绿（`test_integration_real` 仍 1 skipped）。
```bash
git add tests/test_memory_integration.py examples/memory_demo.py
git commit -m "feat: 记忆/RAG 端到端集成测试 + demo"
```

---

## 完成标准（对照规格验收）

- [ ] `add_texts` 后 `search` 召回相关文本并按相似度排序（任务 3/4）
- [ ] 长文本按 chunk_size+overlap 分块入库（任务 1/4）
- [ ] `search_memory` 工具在 agent loop 被调用、结果回填、模型作答（任务 6）
- [ ] `remember` 写入后可被 `search_memory` 召回（任务 5）
- [ ] collection 隔离（任务 3）
- [ ] mock embedder + :memory: store，全程不打网络
- [ ] ①②全部原有测试无回归（`uv run pytest` 全绿）
```

# SP1 记忆内核 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把现有极简向量 RAG 升级为类型化、多租户隔离、过滤下推、可替换后端的记忆内核，作为生产级记忆系统 4 子项目的地基。

**架构：** 新增 `record.py`（类型化数据模型）、`backend.py`（`MemoryBackend` Protocol）、`sqlite_backend.py`（vec0 过滤表 `memory_vec` + companion 数据表 `memory_records`，按 rowid 关联，过滤下推进 KNN）、`migrate.py`（旧库幂等迁移）；把 `memory.py` 门面改造成包 backend 的薄适配器，保持 `add_texts/search` 向后兼容。旧 `store.py`（`MemoryStore`）保留不动。

**技术栈：** Python 3.11+、sqlite-vec 0.1.9（已实测支持 partition key + metadata 下推过滤 + 显式 rowid 插入 + `vec_to_json`）、pytest（`asyncio_mode=auto`）、现有 `mock_embedder` fixture（`tests/conftest.py`，确定性、不打网络）。

**设计规格：** `docs/superpowers/specs/2026-07-11-memory-system-sp1-kernel-design.md`

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/harness/memory/record.py`（新增）| 纯数据类型：`MemType`、`MemoryRecord`、`MemoryFilter`、`MemoryHit` |
| `src/harness/memory/backend.py`（新增）| `MemoryBackend` Protocol（可替换切点，仅接口）|
| `src/harness/memory/sqlite_backend.py`（新增）| `SqliteVecBackend`：建表 + upsert/delete/get/vector_search/keyword_search(占位)/list_by_entity |
| `src/harness/memory/memory.py`（改）| `Memory` 门面改为包 backend；`add_texts/search` 兼容；`collection_to_scope` 映射 |
| `src/harness/memory/migrate.py`（新增）| 旧 `memory.db`（`memory_items`+`memory_vectors`）→ 新表 幂等迁移 |
| `app/assembly.py`（改）| 装配改用 `SqliteVecBackend`，作为 `memory` 与 `memory_store` 传入 |
| `src/harness/memory/store.py`（不动）| 旧 `MemoryStore` + 旧 `MemoryHit` 保留，`test_store.py` 与 `conversation_memory.py` 的导入继续有效 |

**兼容映射约定**（`collection_to_scope`）：`"<kind>:<owner>"` → `(owner, kind)`；无 `:` 的 `"<kind>"` → `("_global", kind)`。例：`"knowledge:u1"`→`(u1, knowledge)`、`"knowledge"`→`(_global, knowledge)`、`"conversation:c1"`→`(c1, conversation)`、`"episodes"`→`(_global, episodes)`。此映射保持现有各消费方的隔离语义不变。

---

## 任务 1：数据模型 `record.py`

**文件：**
- 创建：`src/harness/memory/record.py`
- 测试：`tests/test_memory_record.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_memory_record.py
from harness.memory.record import MemType, MemoryRecord, MemoryFilter, MemoryHit


def test_memtype_values():
    assert MemType.EPISODIC.value == "episodic"
    assert MemType.SEMANTIC.value == "semantic"
    assert MemType.PROCEDURAL.value == "procedural"


def test_record_defaults_and_sentinels():
    r = MemoryRecord(owner_id="u1", kind="knowledge",
                     mem_type=MemType.SEMANTIC, text="hello")
    assert r.id                      # 自动生成非空
    assert r.entity_key == ""        # 哨兵
    assert r.superseded == 0
    assert r.expires_at == 0         # 哨兵：永不过期
    assert r.version == 1
    assert r.importance == 0.5
    assert r.access_count == 0
    assert r.embedding == []
    assert r.created_at and r.last_accessed_at
    assert r.metadata == {}


def test_record_ids_unique():
    a = MemoryRecord(owner_id="u", kind="k", mem_type=MemType.SEMANTIC, text="x")
    b = MemoryRecord(owner_id="u", kind="k", mem_type=MemType.SEMANTIC, text="y")
    assert a.id != b.id


def test_filter_defaults():
    f = MemoryFilter(owner_id="u1")
    assert f.kind is None and f.mem_type is None
    assert f.include_superseded is False and f.include_expired is False


def test_hit_holds_record_and_distance():
    r = MemoryRecord(owner_id="u", kind="k", mem_type=MemType.SEMANTIC, text="t")
    h = MemoryHit(record=r, distance=0.25)
    assert h.record is r and h.distance == 0.25
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_memory_record.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'harness.memory.record'`

- [ ] **步骤 3：编写最少实现代码**

```python
# src/harness/memory/record.py
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class MemType(str, Enum):
    EPISODIC = "episodic"      # 发生过什么（对话、事件）
    SEMANTIC = "semantic"      # 提炼的事实/偏好
    PROCEDURAL = "procedural"  # 学到的做事方法


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MemoryRecord:
    # 身份/隔离
    owner_id: str
    kind: str
    # 分型
    mem_type: MemType
    # 内容
    text: str
    embedding: list[float] = field(default_factory=list)  # 写入必填；检索回读为空
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    # 更新/矛盾（SP3 填；SP1 仅建列）
    entity_key: str = ""       # 空=哨兵
    version: int = 1
    superseded: int = 0        # 0/1
    # 检索打分（SP2 用；SP1 给默认）
    importance: float = 0.5
    created_at: str = field(default_factory=_now_iso)
    last_accessed_at: str = field(default_factory=_now_iso)
    access_count: int = 0
    # 生命周期（SP4 用；SP1 恒 0）
    expires_at: int = 0        # unix 秒；0=永不
    # 溯源
    source: str = ""
    metadata: dict = field(default_factory=dict)


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
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_memory_record.py -q`
预期：PASS（5 passed）

- [ ] **步骤 5：Commit**

```bash
git add src/harness/memory/record.py tests/test_memory_record.py
git commit -m "feat(memory): 类型化记忆数据模型 record.py（SP1）"
```

---

## 任务 2：后端接口 `backend.py`

**文件：**
- 创建：`src/harness/memory/backend.py`
- 测试：`tests/test_memory_backend.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_memory_backend.py
from harness.memory.backend import MemoryBackend


def test_protocol_has_expected_methods():
    for name in ("upsert", "delete", "get",
                 "vector_search", "keyword_search", "list_by_entity"):
        assert hasattr(MemoryBackend, name)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_memory_backend.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'harness.memory.backend'`

- [ ] **步骤 3：编写最少实现代码**

```python
# src/harness/memory/backend.py
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .record import MemoryFilter, MemoryHit, MemoryRecord


@runtime_checkable
class MemoryBackend(Protocol):
    """记忆存储可替换切点。SqliteVecBackend 实现之；日后 PgVector/Qdrant 实现同签名即可切换。"""

    def upsert(self, records: list[MemoryRecord]) -> list[str]:
        """按 id 存在则覆盖、不存在则插入，返回 id 列表。"""
        ...

    def delete(self, ids: list[str]) -> None: ...

    def get(self, ids: list[str]) -> list[MemoryRecord]: ...

    def vector_search(self, query_embedding: list[float], *,
                      filters: MemoryFilter, k: int) -> list[MemoryHit]:
        """向量 KNN，filters 下推进 KNN（分区键 + metadata 列），非查后过滤。"""
        ...

    def keyword_search(self, query_text: str, *,
                       filters: MemoryFilter, k: int) -> list[MemoryHit]:
        """SP1 占位返回空；FTS5 真实现留 SP2（签名先定死）。"""
        ...

    def list_by_entity(self, owner_id: str, kind: str,
                       entity_key: str) -> list[MemoryRecord]:
        """按实体键查同一实体的记录（SP3 upsert-by-entity 用）。"""
        ...
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_memory_backend.py -q`
预期：PASS（1 passed）

- [ ] **步骤 5：Commit**

```bash
git add src/harness/memory/backend.py tests/test_memory_backend.py
git commit -m "feat(memory): MemoryBackend 可替换接口（SP1）"
```

---

## 任务 3：SQLite 后端 `sqlite_backend.py`

**文件：**
- 创建：`src/harness/memory/sqlite_backend.py`
- 测试：`tests/test_sqlite_backend.py`

> 关键：`owner_id` 为 vec0 partition key（隔离），`kind/mem_type/superseded` 为 metadata 列（KNN 内下推过滤）；companion 表 `memory_records` 存业务字段，按 rowid 对齐。**过滤在 KNN 完成，无 Python 后过滤。** SP1 不做 `expires_at` 过滤（恒 0，留 SP4）。

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_sqlite_backend.py
import pytest

from harness.memory.record import MemType, MemoryFilter, MemoryRecord
from harness.memory.sqlite_backend import SqliteVecBackend


def _rec(owner, kind, text, vec, mem_type=MemType.SEMANTIC, entity_key="", rid=None):
    r = MemoryRecord(owner_id=owner, kind=kind, mem_type=mem_type,
                     text=text, embedding=vec, entity_key=entity_key)
    if rid:
        r.id = rid
    return r


def _backend():
    return SqliteVecBackend(":memory:", dimension=3)


def test_partition_isolation():
    b = _backend()
    b.upsert([_rec("u1", "knowledge", "u1 secret", [1.0, 0.0, 0.0])])
    b.upsert([_rec("u2", "knowledge", "u2 secret", [1.0, 0.0, 0.0])])
    hits = b.vector_search([1.0, 0.0, 0.0], filters=MemoryFilter(owner_id="u2"), k=5)
    assert [h.record.text for h in hits] == ["u2 secret"]   # 绝不召回 u1


def test_kind_filter_pushdown():
    b = _backend()
    b.upsert([_rec("u1", "knowledge", "kb", [1.0, 0.0, 0.0])])
    b.upsert([_rec("u1", "conversation", "conv", [1.0, 0.0, 0.0])])
    hits = b.vector_search([1.0, 0.0, 0.0],
                           filters=MemoryFilter(owner_id="u1", kind="conversation"), k=5)
    assert [h.record.text for h in hits] == ["conv"]


def test_mem_type_filter_pushdown():
    b = _backend()
    b.upsert([_rec("u1", "k", "sem", [1.0, 0.0, 0.0], mem_type=MemType.SEMANTIC)])
    b.upsert([_rec("u1", "k", "epi", [1.0, 0.0, 0.0], mem_type=MemType.EPISODIC)])
    hits = b.vector_search([1.0, 0.0, 0.0],
                           filters=MemoryFilter(owner_id="u1", mem_type="episodic"), k=5)
    assert [h.record.text for h in hits] == ["epi"]


def test_upsert_overwrites_same_id():
    b = _backend()
    b.upsert([_rec("u1", "k", "v1", [1.0, 0.0, 0.0], rid="fixed")])
    b.upsert([_rec("u1", "k", "v2", [1.0, 0.0, 0.0], rid="fixed")])
    got = b.get(["fixed"])
    assert len(got) == 1 and got[0].text == "v2"


def test_delete_removes_from_both_tables():
    b = _backend()
    b.upsert([_rec("u1", "k", "x", [1.0, 0.0, 0.0], rid="d1")])
    b.delete(["d1"])
    assert b.get(["d1"]) == []
    assert b.vector_search([1.0, 0.0, 0.0], filters=MemoryFilter(owner_id="u1"), k=5) == []


def test_list_by_entity():
    b = _backend()
    b.upsert([_rec("u1", "k", "lang=zh", [1.0, 0.0, 0.0], entity_key="pref.lang")])
    b.upsert([_rec("u1", "k", "unrelated", [0.0, 1.0, 0.0], entity_key="")])
    recs = b.list_by_entity("u1", "k", "pref.lang")
    assert [r.text for r in recs] == ["lang=zh"]


def test_superseded_excluded_by_default():
    b = _backend()
    r = _rec("u1", "k", "old", [1.0, 0.0, 0.0], rid="s1")
    r.superseded = 1
    b.upsert([r])
    assert b.vector_search([1.0, 0.0, 0.0], filters=MemoryFilter(owner_id="u1"), k=5) == []
    incl = b.vector_search([1.0, 0.0, 0.0],
                           filters=MemoryFilter(owner_id="u1", include_superseded=True), k=5)
    assert [h.record.text for h in incl] == ["old"]


def test_dimension_mismatch_raises():
    b = _backend()
    with pytest.raises(ValueError):
        b.upsert([_rec("u1", "k", "bad", [1.0, 0.0])])   # 2 维，期望 3


def test_keyword_search_placeholder_returns_empty():
    b = _backend()
    b.upsert([_rec("u1", "k", "hello world", [1.0, 0.0, 0.0])])
    assert b.keyword_search("hello", filters=MemoryFilter(owner_id="u1"), k=5) == []
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_sqlite_backend.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'harness.memory.sqlite_backend'`

- [ ] **步骤 3：编写最少实现代码**

```python
# src/harness/memory/sqlite_backend.py
from __future__ import annotations

import json
import sqlite3

import sqlite_vec
from sqlite_vec import serialize_float32

from .record import MemoryFilter, MemoryHit, MemoryRecord, MemType

# memory_records 的列顺序（rowid 之外），供 INSERT / 回读复用
_COLS = ("id", "owner_id", "kind", "mem_type", "text", "entity_key", "version",
         "superseded", "importance", "created_at", "last_accessed_at",
         "access_count", "expires_at", "source", "metadata")


class SqliteVecBackend:
    """MemoryBackend 的 sqlite-vec 实现：vec0 过滤表 + companion 数据表，rowid 对齐。"""

    def __init__(self, db_path: str, dimension: int) -> None:
        self._dim = dimension
        self._conn = sqlite3.connect(db_path)
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)
        self._conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0("
            f"owner_id TEXT partition key, kind TEXT, mem_type TEXT, "
            f"superseded INTEGER, expires_at INTEGER, embedding float[{dimension}])")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS memory_records("
            "rowid INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, "
            "owner_id TEXT NOT NULL, kind TEXT NOT NULL, mem_type TEXT NOT NULL, "
            "text TEXT NOT NULL, entity_key TEXT NOT NULL DEFAULT '', "
            "version INTEGER NOT NULL DEFAULT 1, superseded INTEGER NOT NULL DEFAULT 0, "
            "importance REAL NOT NULL DEFAULT 0.5, created_at TEXT NOT NULL, "
            "last_accessed_at TEXT NOT NULL, access_count INTEGER NOT NULL DEFAULT 0, "
            "expires_at INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL DEFAULT '', "
            "metadata TEXT NOT NULL DEFAULT '{}')")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_mem_entity "
            "ON memory_records(owner_id, kind, entity_key)")
        self._conn.commit()

    # ---- 写 ----
    def upsert(self, records: list[MemoryRecord]) -> list[str]:
        ids: list[str] = []
        for r in records:
            if len(r.embedding) != self._dim:
                raise ValueError(f"向量维度不符：期望 {self._dim}，收到 {len(r.embedding)}")
            old = self._conn.execute(
                "SELECT rowid FROM memory_records WHERE id = ?", (r.id,)).fetchone()
            if old is not None:
                self._delete_rowid(old[0])            # 覆盖：先删旧行（两表）
            cur = self._conn.execute(
                f"INSERT INTO memory_records({','.join(_COLS)}) "
                f"VALUES ({','.join('?' * len(_COLS))})",
                (r.id, r.owner_id, r.kind, r.mem_type.value, r.text, r.entity_key,
                 r.version, r.superseded, r.importance, r.created_at,
                 r.last_accessed_at, r.access_count, r.expires_at, r.source,
                 json.dumps(r.metadata, ensure_ascii=False)))
            rowid = cur.lastrowid
            self._conn.execute(
                "INSERT INTO memory_vec(rowid, owner_id, kind, mem_type, superseded, "
                "expires_at, embedding) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rowid, r.owner_id, r.kind, r.mem_type.value, r.superseded,
                 r.expires_at, serialize_float32(r.embedding)))
            ids.append(r.id)
        self._conn.commit()
        return ids

    def delete(self, ids: list[str]) -> None:
        for i in ids:
            row = self._conn.execute(
                "SELECT rowid FROM memory_records WHERE id = ?", (i,)).fetchone()
            if row is not None:
                self._delete_rowid(row[0])
        self._conn.commit()

    def _delete_rowid(self, rowid: int) -> None:
        self._conn.execute("DELETE FROM memory_records WHERE rowid = ?", (rowid,))
        self._conn.execute("DELETE FROM memory_vec WHERE rowid = ?", (rowid,))

    # ---- 读 ----
    def get(self, ids: list[str]) -> list[MemoryRecord]:
        out: list[MemoryRecord] = []
        for i in ids:
            row = self._conn.execute(
                f"SELECT {','.join(_COLS)} FROM memory_records WHERE id = ?", (i,)).fetchone()
            if row is not None:
                out.append(self._row_to_record(row))
        return out

    def list_by_entity(self, owner_id: str, kind: str,
                       entity_key: str) -> list[MemoryRecord]:
        rows = self._conn.execute(
            f"SELECT {','.join(_COLS)} FROM memory_records "
            "WHERE owner_id = ? AND kind = ? AND entity_key = ? AND superseded = 0",
            (owner_id, kind, entity_key)).fetchall()
        return [self._row_to_record(r) for r in rows]

    def vector_search(self, query_embedding: list[float], *,
                      filters: MemoryFilter, k: int) -> list[MemoryHit]:
        conds = ["embedding MATCH ?", "k = ?", "owner_id = ?"]
        params: list = [serialize_float32(query_embedding), k, filters.owner_id]
        if filters.kind is not None:
            conds.append("kind = ?"); params.append(filters.kind)
        if filters.mem_type is not None:
            conds.append("mem_type = ?"); params.append(filters.mem_type)
        if not filters.include_superseded:
            conds.append("superseded = ?"); params.append(0)
        # 注：SP1 不过滤 expires_at（恒 0），TTL 剔除留 SP4。
        sql = ("SELECT rowid, distance FROM memory_vec WHERE "
               + " AND ".join(conds) + " ORDER BY distance")
        rows = self._conn.execute(sql, params).fetchall()
        hits: list[MemoryHit] = []
        for rowid, distance in rows:
            rec = self._conn.execute(
                f"SELECT {','.join(_COLS)} FROM memory_records WHERE rowid = ?",
                (rowid,)).fetchone()
            if rec is not None:
                hits.append(MemoryHit(record=self._row_to_record(rec), distance=distance))
        return hits

    def keyword_search(self, query_text: str, *,
                       filters: MemoryFilter, k: int) -> list[MemoryHit]:
        return []   # TODO(SP2): FTS5 关键词检索

    def _row_to_record(self, row: tuple) -> MemoryRecord:
        d = dict(zip(_COLS, row))
        return MemoryRecord(
            owner_id=d["owner_id"], kind=d["kind"], mem_type=MemType(d["mem_type"]),
            text=d["text"], embedding=[], id=d["id"], entity_key=d["entity_key"],
            version=d["version"], superseded=d["superseded"], importance=d["importance"],
            created_at=d["created_at"], last_accessed_at=d["last_accessed_at"],
            access_count=d["access_count"], expires_at=d["expires_at"],
            source=d["source"], metadata=json.loads(d["metadata"] or "{}"))

    def close(self) -> None:
        self._conn.close()
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_sqlite_backend.py -q`
预期：PASS（9 passed）

- [ ] **步骤 5：Commit**

```bash
git add src/harness/memory/sqlite_backend.py tests/test_sqlite_backend.py
git commit -m "feat(memory): SqliteVecBackend 分区隔离+过滤下推（SP1）"
```

---

## 任务 4：门面改造 `memory.py`（向后兼容）

**文件：**
- 修改：`src/harness/memory/memory.py`（整体重写为包 backend）
- 修改：`tests/test_memory.py`（把 `Memory(MemoryStore(...), ...)` 改为 `Memory(SqliteVecBackend(...), ...)`）
- 测试：`tests/test_memory_facade.py`（新增：collection→scope 映射 + 隔离）

> `Memory` 保持 `add_texts(texts, collection, metadata)` / `search(query, collection, k)` 签名与返回形状（旧 `MemoryHit`，来自 `store.py`），消费方 `KnowledgeService`/`QuizService`/`ConversationMemoryService`/agent 工具零改动。构造参数由「旧 MemoryStore」改为「MemoryBackend」。

- [ ] **步骤 1：编写失败的测试（新增映射测试 + 改造现有 test_memory.py）**

新增 `tests/test_memory_facade.py`：

```python
# tests/test_memory_facade.py
from harness.memory.memory import Memory, collection_to_scope
from harness.memory.sqlite_backend import SqliteVecBackend


def test_collection_to_scope_mapping():
    assert collection_to_scope("knowledge:u1") == ("u1", "knowledge")
    assert collection_to_scope("conversation:c1") == ("c1", "conversation")
    assert collection_to_scope("knowledge") == ("_global", "knowledge")
    assert collection_to_scope("episodes") == ("_global", "episodes")


async def test_add_and_search_roundtrip(mock_embedder):
    backend = SqliteVecBackend(":memory:", dimension=64)
    mem = Memory(backend, mock_embedder(dimension=64), chunk_size=1000, overlap=0)
    await mem.add_texts(["python programming language"], "knowledge:u1")
    await mem.add_texts(["the cat sat on the mat"], "knowledge:u1")
    hits = await mem.search("cat", "knowledge:u1", k=1)
    assert hits[0].text == "the cat sat on the mat"
    assert hits[0].collection == "knowledge:u1"     # 回填原 collection 字符串


async def test_search_isolated_by_owner(mock_embedder):
    backend = SqliteVecBackend(":memory:", dimension=64)
    mem = Memory(backend, mock_embedder(dimension=64), chunk_size=1000, overlap=0)
    await mem.add_texts(["the cat sat on the mat"], "knowledge:u1")
    assert await mem.search("cat", "knowledge:u2", k=5) == []   # 不串租户
```

改造 `tests/test_memory.py`：把两处后端构造替换（其余断言不变）：

```python
# tests/test_memory.py —— 顶部 import 与后端构造改造
from harness.memory.memory import Memory
from harness.memory.sqlite_backend import SqliteVecBackend


async def test_add_texts_chunks_and_stores(mock_embedder):
    backend = SqliteVecBackend(":memory:", dimension=64)
    mem = Memory(backend, mock_embedder(dimension=64), chunk_size=100, overlap=20)
    long_text = "词 " * 200
    ids = await mem.add_texts([long_text], "knowledge")
    assert len(ids) >= 2


async def test_search_recalls_relevant(mock_embedder):
    backend = SqliteVecBackend(":memory:", dimension=64)
    mem = Memory(backend, mock_embedder(dimension=64), chunk_size=1000, overlap=0)
    await mem.add_texts(["python programming language"], "knowledge")
    await mem.add_texts(["the cat sat on the mat"], "knowledge")
    hits = await mem.search("cat", "knowledge", k=1)
    assert hits[0].text == "the cat sat on the mat"


async def test_add_empty_text_returns_empty(mock_embedder):
    backend = SqliteVecBackend(":memory:", dimension=64)
    mem = Memory(backend, mock_embedder(dimension=64), chunk_size=100, overlap=20)
    assert await mem.add_texts(["   "], "knowledge") == []
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_memory_facade.py tests/test_memory.py -q`
预期：FAIL，`ImportError: cannot import name 'collection_to_scope'`（及 `Memory` 仍要求旧 store 接口）

- [ ] **步骤 3：编写实现（整体重写 memory.py）**

```python
# src/harness/memory/memory.py
from __future__ import annotations

from ..memory.store import MemoryHit          # 保留旧返回形状，消费方零改动
from .backend import MemoryBackend
from .chunker import chunk
from .embeddings import EmbeddingClient
from .record import MemoryRecord, MemType


def collection_to_scope(collection: str) -> tuple[str, str]:
    """旧 collection 字符串 → (owner_id, kind)。"<kind>:<owner>" / "<kind>"（无 owner→_global）。"""
    if ":" in collection:
        kind, owner = collection.split(":", 1)
        return owner, kind
    return "_global", collection


class Memory:
    """串起 chunker + embedder + backend 的门面。保留 add_texts/search 兼容签名。"""

    def __init__(self, backend: MemoryBackend, embedder: EmbeddingClient,
                 chunk_size: int = 1000, overlap: int = 200) -> None:
        self._backend = backend
        self._embedder = embedder
        self._chunk_size = chunk_size
        self._overlap = overlap

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
        from .record import MemoryFilter
        owner_id, kind = collection_to_scope(collection)
        vectors = await self._embedder.embed([query])
        hits = self._backend.vector_search(
            vectors[0], filters=MemoryFilter(owner_id=owner_id, kind=kind), k=k)
        return [MemoryHit(text=h.record.text, collection=collection,
                          metadata=h.record.metadata, distance=h.distance)
                for h in hits]
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_memory_facade.py tests/test_memory.py -q`
预期：PASS（4 + 3 passed）

- [ ] **步骤 5：Commit**

```bash
git add src/harness/memory/memory.py tests/test_memory_facade.py tests/test_memory.py
git commit -m "feat(memory): Memory 门面改包 backend，保持向后兼容（SP1）"
```

---

## 任务 5：装配改线 `app/assembly.py`

**文件：**
- 修改：`app/assembly.py:73-86`（用 `SqliteVecBackend` 取代 `MemoryStore` 作为 `Memory` 后端与 `memory_store`）
- 测试：现有 `tests/app/test_knowledge.py`、`tests/app/test_conversation_memory.py`、`tests/test_memory_tools.py`、`tests/test_episodic.py`、quiz 相关

> `KnowledgeService.delete` 走 `memory_store.delete(chunk_ids)`；改线后 `memory_store` = backend，`chunk_ids` = `add_texts` 返回的字符串 id，`backend.delete(list[str])` 生效。消费方代码不动。

- [ ] **步骤 1：先跑现有相关测试，记录当前 PASS 基线**

运行：`.venv/bin/python -m pytest tests/app/test_knowledge.py tests/app/test_conversation_memory.py tests/test_memory_tools.py tests/test_episodic.py -q`
预期：当前基于旧 store 全 PASS（作为改线前基线；改线后必须仍全绿）

- [ ] **步骤 2：改装配代码**

把 `app/assembly.py` 中（约 73-86 行）的：

```python
        from harness.memory.store import MemoryStore
        ...
        mem_store = MemoryStore(config.memory_db_path, config.embedding_dimension)
        mem = Memory(mem_store, embedder, config.chunk_size, config.chunk_overlap)
        ...
        memory_store = mem_store
```

替换为：

```python
        from harness.memory.sqlite_backend import SqliteVecBackend
        ...
        mem_store = SqliteVecBackend(config.memory_db_path, config.embedding_dimension)
        mem = Memory(mem_store, embedder, config.chunk_size, config.chunk_overlap)
        ...
        memory_store = mem_store
```

（删除 `from harness.memory.store import MemoryStore` 这一行导入；其余 embedder / EpisodicMemory / 工具注册不变。）

- [ ] **步骤 3：运行相关测试验证仍全绿**

运行：`.venv/bin/python -m pytest tests/app/test_knowledge.py tests/app/test_conversation_memory.py tests/test_memory_tools.py tests/test_episodic.py tests/app/test_quiz_generate.py -q`
预期：PASS（与基线一致，无回归）

- [ ] **步骤 4：Commit**

```bash
git add app/assembly.py
git commit -m "feat(memory): 装配改用 SqliteVecBackend（SP1）"
```

---

## 任务 6：旧库迁移 `migrate.py`

**文件：**
- 创建：`src/harness/memory/migrate.py`
- 测试：`tests/test_memory_migrate.py`

> 读旧 `memory_items`(id, collection, text, metadata) + `memory_vectors`(rowid=id, embedding)，用 `vec_to_json` 反序列化向量，按 `collection_to_scope` 拆 owner/kind，mem_type=semantic、importance=0.5，`backend.upsert` 进新表。幂等靠旧库内 `_memory_migrations` 标记表。

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_memory_migrate.py
import sqlite3

import sqlite_vec
from sqlite_vec import serialize_float32

from harness.memory.migrate import migrate_memory_db
from harness.memory.record import MemoryFilter
from harness.memory.sqlite_backend import SqliteVecBackend


def _make_old_db(path):
    """造一个旧格式 memory.db：memory_items + memory_vectors。"""
    c = sqlite3.connect(path)
    c.enable_load_extension(True); sqlite_vec.load(c); c.enable_load_extension(False)
    c.execute("CREATE VIRTUAL TABLE memory_vectors USING vec0(embedding float[3])")
    c.execute("CREATE TABLE memory_items(id INTEGER PRIMARY KEY, collection TEXT NOT NULL, "
              "text TEXT NOT NULL, metadata TEXT, created_at TEXT NOT NULL)")
    rows = [("knowledge:u1", "python language", [1.0, 0.0, 0.0]),
            ("conversation:c1", "the cat sat", [0.0, 1.0, 0.0])]
    for coll, text, vec in rows:
        cur = c.execute("INSERT INTO memory_items(collection, text, metadata, created_at) "
                        "VALUES (?, ?, '{}', 'now')", (coll, text))
        c.execute("INSERT INTO memory_vectors(rowid, embedding) VALUES (?, ?)",
                  (cur.lastrowid, serialize_float32(vec)))
    c.commit(); c.close()


def test_migrate_moves_rows(tmp_path):
    old = str(tmp_path / "memory.db")
    _make_old_db(old)
    backend = SqliteVecBackend(":memory:", dimension=3)
    n = migrate_memory_db(old, backend)
    assert n == 2
    # 迁移后可按新隔离/kind 检索
    hits = backend.vector_search([1.0, 0.0, 0.0],
                                 filters=MemoryFilter(owner_id="u1", kind="knowledge"), k=5)
    assert [h.record.text for h in hits] == ["python language"]
    conv = backend.vector_search([0.0, 1.0, 0.0],
                                 filters=MemoryFilter(owner_id="c1", kind="conversation"), k=5)
    assert [h.record.text for h in conv] == ["the cat sat"]


def test_migrate_is_idempotent(tmp_path):
    old = str(tmp_path / "memory.db")
    _make_old_db(old)
    backend = SqliteVecBackend(":memory:", dimension=3)
    assert migrate_memory_db(old, backend) == 2
    assert migrate_memory_db(old, backend) == 0        # 二次迁移不重复
    hits = backend.vector_search([1.0, 0.0, 0.0],
                                 filters=MemoryFilter(owner_id="u1", kind="knowledge"), k=5)
    assert len(hits) == 1                              # 无重复行
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_memory_migrate.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'harness.memory.migrate'`

- [ ] **步骤 3：编写实现**

```python
# src/harness/memory/migrate.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import sqlite_vec

from .backend import MemoryBackend
from .memory import collection_to_scope
from .record import MemoryRecord, MemType

_MARK = "sp1_kernel"


def migrate_memory_db(old_path: str, backend: MemoryBackend) -> int:
    """把旧 memory.db 的行迁入 backend。幂等：已迁移返回 0。返回迁移条数。"""
    conn = sqlite3.connect(old_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("CREATE TABLE IF NOT EXISTS _memory_migrations("
                 "name TEXT PRIMARY KEY, done_at TEXT)")
    if conn.execute("SELECT 1 FROM _memory_migrations WHERE name = ?", (_MARK,)).fetchone():
        conn.close()
        return 0
    rows = conn.execute(
        "SELECT id, collection, text, metadata FROM memory_items").fetchall()
    records: list[MemoryRecord] = []
    for old_id, collection, text, meta in rows:
        emb = conn.execute(
            "SELECT vec_to_json(embedding) FROM memory_vectors WHERE rowid = ?",
            (old_id,)).fetchone()
        if emb is None:
            continue
        owner_id, kind = collection_to_scope(collection)
        records.append(MemoryRecord(
            owner_id=owner_id, kind=kind, mem_type=MemType.SEMANTIC, text=text,
            embedding=json.loads(emb[0]), metadata=json.loads(meta or "{}"),
            source="migrated:sp1"))
    backend.upsert(records)
    conn.execute("INSERT INTO _memory_migrations(name, done_at) VALUES (?, ?)",
                 (_MARK, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    return len(records)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_memory_migrate.py -q`
预期：PASS（2 passed）

- [ ] **步骤 5：Commit**

```bash
git add src/harness/memory/migrate.py tests/test_memory_migrate.py
git commit -m "feat(memory): 旧 memory.db 幂等迁移到新内核（SP1）"
```

---

## 任务 7：全仓回归 + 收尾

- [ ] **步骤 1：跑记忆相关全量**

运行：`.venv/bin/python -m pytest tests/test_memory_record.py tests/test_memory_backend.py tests/test_sqlite_backend.py tests/test_memory_facade.py tests/test_memory.py tests/test_store.py tests/test_memory_migrate.py tests/test_memory_tools.py tests/test_episodic.py tests/app/test_knowledge.py tests/app/test_conversation_memory.py -q`
预期：全 PASS（`test_store.py` 因旧 `MemoryStore` 保留而仍绿）

- [ ] **步骤 2：跑全仓回归（排除外部依赖集成测试）**

运行：
```bash
.venv/bin/python -m pytest tests/ -q -p no:cacheprovider \
  --deselect tests/test_integration_real.py \
  --ignore=tests/test_docker_sandbox.py \
  --ignore=tests/test_sandboxed_browser.py \
  --ignore=tests/test_sandboxed_browser_integration.py \
  --ignore=tests/test_browser_playwright.py \
  --ignore=tests/test_browser_integration.py \
  --ignore=tests/test_routing_sandbox.py -k "not real"
```
预期：仅既有的 2 个无关失败（`test_assembly.py::test_no_lang_images_no_multilang_code_tools`、`test_sandbox_manager.py::test_proxy_exposes_sandbox_for_only_when_routing`，均为上个 commit 遗留的 sandbox 测试，与本变更无关）；其余全绿。

- [ ] **步骤 3：最终 commit（若步骤 1/2 有微调）**

```bash
git add -A
git commit -m "test(memory): SP1 记忆内核全量回归通过"
```

---

## 自检结论

- **规格覆盖**：数据模型(任务1)、后端接口(任务2)、vec0 隔离+下推+companion(任务3)、门面兼容+collection 映射(任务4)、装配改线(任务5)、幂等迁移(任务6)、回归(任务7)——规格 §1 IN 全覆盖；§1 OUT（rerank/关键词/TTL 剔除/矛盾判定）明确不实现，仅建列/占位。
- **类型一致**：`MemoryRecord`/`MemType`/`MemoryFilter`/`MemoryHit`（record.py）贯穿全程；`SqliteVecBackend` 方法签名与 `MemoryBackend` Protocol 一致；门面返回旧 `store.MemoryHit`（消费方形状不变），与后端 `record.MemoryHit`（record+distance）显式区分、在 `Memory.search` 内映射。
- **无占位符**：每步含可运行代码与精确命令；`keyword_search` 的空实现是**规格明确的 SP1 决定**（非计划缺陷），带 `TODO(SP2)`。
- **兼容边界**：`store.py` 保留 → `test_store.py` 与 `conversation_memory.py` 的 `MemoryHit` 导入不受影响。

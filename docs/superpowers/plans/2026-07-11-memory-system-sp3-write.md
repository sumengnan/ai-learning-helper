> ⚠️ **历史设计记录（已过时）**：harness 内核已抽成外部包
> [ai-harness-framework](https://github.com/sumengnan/ai-harness-framework)（import 名仍是 `harness`）。
> 本文是带日期的设计存档，文中的 `src/harness/` 路径与打包配置反映**当时**的仓库结构、未随抽包更新；
> 当前结构以 [架构文档](../../architecture-harness.md) 为准。

# SP3 写入提炼层 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把机械转储写入升级为 LLM 驱动的智能写入：提炼事实 → 找候选旧记忆 → 调和（ADD/NOOP/REPLACE）→ 确定性应用，矛盾自动新胜（旧标 superseded）。

**架构：** 新增 `writer.py`（`MemoryWriter` + `ExtractedFact`/`MemoryOp` + 提炼/调和 prompt + 稳健 JSON 解析）；`SqliteVecBackend.set_superseded`（vec0 metadata UPDATE，已实测支持）；`ConversationMemoryService` 增智能写入路径，config 门控默认关（默认走 SP1 原文入库）。两次 LLM 调用（提炼 + 批量调和），解析失败安全降级。

**技术栈：** Python 3.11+、sqlite-vec 0.1.9、pytest（`asyncio_mode=auto`）、`mock_embedder`（`tests/conftest.py`）+ 自定义 scripted LLM completer（不打网络）。复用 `build_completer`（`app/completion.py`）、SP2 `Retriever`、`backend.list_by_entity/upsert/get`。

**设计规格：** `docs/superpowers/specs/2026-07-11-memory-system-sp3-write-design.md`

**工作区注意：** 本计划在 worktree `.claude/worktrees/feat+memory-sp3-write` 执行。跑测试**必须**带 `PYTHONPATH="$PWD/src:$PWD"`：
`PYTHONPATH="$PWD/src:$PWD" /Users/sumengnan/PycharmProjects/ai-learning-helper/.venv/bin/python -m pytest <args>`
（下文简写 `PYTHONPATH=... pytest`。）

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/harness/memory/sqlite_backend.py`（改）| `set_superseded(ids)`（UPDATE 两表 superseded=1）|
| `src/harness/memory/backend.py`（改）| Protocol 加 `set_superseded` 签名 |
| `src/harness/memory/writer.py`（新增）| `ExtractedFact`/`MemoryOp`/`MemoryWriter` + prompt + JSON 解析 |
| `app/conversation_memory.py`（改）| 增可选 `writer` + `sample_rate`，智能写入路径，默认走原 `add_texts` |
| `app/config.py`（改）| `memory_write_extract` 等配置 |
| `app/assembly.py`（改）| 构造 `MemoryWriter` 挂到 harness（config 启用时）|
| `app/api/chat.py`（改）| `ConversationMemoryService` 构造处传入 writer |

---

## 任务 1：backend.set_superseded

**文件：**
- 修改：`src/harness/memory/sqlite_backend.py`
- 修改：`src/harness/memory/backend.py`
- 测试：`tests/test_backend_superseded.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_backend_superseded.py
from harness.memory.record import MemType, MemoryFilter, MemoryRecord
from harness.memory.sqlite_backend import SqliteVecBackend


def _rec(rid, vec=(1.0, 0.0, 0.0)):
    return MemoryRecord(owner_id="u1", kind="k", mem_type=MemType.SEMANTIC,
                        text=f"fact {rid}", embedding=list(vec), id=rid)


def test_set_superseded_excludes_from_search():
    b = SqliteVecBackend(":memory:", dimension=3)
    b.upsert([_rec("a"), _rec("b", (0.0, 1.0, 0.0))])
    b.set_superseded(["a"])
    hits = b.vector_search([1.0, 0.0, 0.0], filters=MemoryFilter(owner_id="u1"), k=5)
    assert "a" not in [h.record.id for h in hits]                 # 默认检索排除
    incl = b.vector_search([1.0, 0.0, 0.0],
                           filters=MemoryFilter(owner_id="u1", include_superseded=True), k=5)
    assert "a" in [h.record.id for h in incl]                    # 显式包含可见


def test_set_superseded_get_still_returns():
    b = SqliteVecBackend(":memory:", dimension=3)
    b.upsert([_rec("a")])
    b.set_superseded(["a"])
    got = b.get(["a"])
    assert len(got) == 1 and got[0].superseded == 1              # 行保留，标志置 1


def test_set_superseded_missing_id_noop():
    b = SqliteVecBackend(":memory:", dimension=3)
    b.set_superseded(["nope"])                                   # 不存在的 id 静默跳过，不抛错
```

- [ ] **步骤 2：运行验证失败**

运行：`PYTHONPATH=... pytest tests/test_backend_superseded.py -q`
预期：FAIL（`set_superseded` 不存在 → AttributeError）

- [ ] **步骤 3：实现**

`src/harness/memory/sqlite_backend.py`——在 `_delete_rowid` 之后（或 `get` 附近）加方法：
```python
    def set_superseded(self, ids: list[str]) -> None:
        for i in ids:
            row = self._conn.execute(
                "SELECT rowid FROM memory_records WHERE id = ?", (i,)).fetchone()
            if row is None:
                continue
            self._conn.execute(
                "UPDATE memory_records SET superseded = 1 WHERE rowid = ?", (row[0],))
            self._conn.execute(
                "UPDATE memory_vec SET superseded = 1 WHERE rowid = ?", (row[0],))
        self._conn.commit()
```

`src/harness/memory/backend.py`——Protocol 内加签名：
```python
    def set_superseded(self, ids: list[str]) -> None:
        """把指定记录标记 superseded=1（检索默认排除，行保留可恢复）。"""
        ...
```

- [ ] **步骤 4：运行验证通过**

运行：`PYTHONPATH=... pytest tests/test_backend_superseded.py tests/test_sqlite_backend.py -q`
预期：PASS（新 3 + SP1/SP2 既有全绿）

- [ ] **步骤 5：Commit**

```bash
git add src/harness/memory/sqlite_backend.py src/harness/memory/backend.py tests/test_backend_superseded.py
git commit -m "feat(memory): backend.set_superseded（SP3）"
```

---

## 任务 2：writer 数据类型 + 提炼 `_extract`

**文件：**
- 创建：`src/harness/memory/writer.py`
- 测试：`tests/test_memory_writer.py`

> 本任务建数据类型 + `_extract`（LLM#1）+ JSON 解析工具。`MemoryWriter` 类骨架也在此建（`__init__` + `_extract`），`_gather_candidates`/`_reconcile`/`_apply`/`write` 在任务 3、4 补齐。

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_memory_writer.py
from harness.memory.record import MemType
from harness.memory.writer import ExtractedFact, MemoryWriter, _parse_facts


class ScriptedCompleter:
    """按顺序返回预设响应的假 LLM completer（async (sys, user) -> str）。"""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
    async def __call__(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        return self._responses.pop(0)


def test_parse_facts_valid():
    raw = '[{"text":"用户偏好中文","mem_type":"semantic","entity_key":"user.pref.lang","importance":0.8}]'
    facts = _parse_facts(raw)
    assert len(facts) == 1
    assert facts[0].text == "用户偏好中文"
    assert facts[0].mem_type == MemType.SEMANTIC
    assert facts[0].entity_key == "user.pref.lang"
    assert facts[0].importance == 0.8


def test_parse_facts_code_fence():
    raw = '```json\n[{"text":"事实","mem_type":"episodic"}]\n```'
    facts = _parse_facts(raw)
    assert len(facts) == 1 and facts[0].mem_type == MemType.EPISODIC
    assert facts[0].importance == 0.5 and facts[0].entity_key == ""      # 默认值


def test_parse_facts_invalid_json_returns_empty():
    assert _parse_facts("对不起我不会") == []
    assert _parse_facts('{"not":"a list"}') == []


def test_parse_facts_bad_memtype_defaults_semantic():
    facts = _parse_facts('[{"text":"x","mem_type":"weird"}]')
    assert facts[0].mem_type == MemType.SEMANTIC


async def test_extract_calls_llm_and_parses(mock_embedder):
    comp = ScriptedCompleter(['[{"text":"用户在学 Python","mem_type":"semantic","importance":0.7}]'])
    w = MemoryWriter(backend=None, embedder=mock_embedder(dimension=64),
                     retriever=None, complete=comp)
    facts = await w._extract("我最近在学 Python")
    assert len(facts) == 1 and facts[0].text == "用户在学 Python"
    assert len(comp.calls) == 1


async def test_extract_empty_on_llm_garbage(mock_embedder):
    comp = ScriptedCompleter(["这不是 JSON"])
    w = MemoryWriter(backend=None, embedder=mock_embedder(dimension=64),
                     retriever=None, complete=comp)
    assert await w._extract("闲聊") == []
```

- [ ] **步骤 2：运行验证失败**

运行：`PYTHONPATH=... pytest tests/test_memory_writer.py -q`
预期：FAIL（ModuleNotFoundError）

- [ ] **步骤 3：实现** `src/harness/memory/writer.py`：

```python
# src/harness/memory/writer.py
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from .record import MemType, MemoryRecord

log = logging.getLogger(__name__)

_EXTRACT_SYS = (
    "你是记忆提炼器。从下面的文本中提炼**值得长期记住**的事实——"
    "用户偏好、目标、稳定属性、学到的方法或结论；忽略寒暄、一次性内容、临时上下文。"
    "输出 JSON 数组，每个元素：{\"text\": 简洁事实, \"mem_type\": "
    "\"semantic\"|\"episodic\"|\"procedural\", \"entity_key\": 点分实体键如 "
    "\"user.pref.language\"（无明确实体则空串）, \"importance\": 0~1 的浮点}。"
    "没有值得记的内容就输出 []。只输出 JSON，不要解释。"
)


@dataclass
class ExtractedFact:
    text: str
    mem_type: MemType
    entity_key: str = ""
    importance: float = 0.5


@dataclass
class MemoryOp:
    op: str                              # "ADD" | "NOOP" | "REPLACE"
    fact: ExtractedFact | None           # 由 fact_index 解析而来；NOOP 也带（应用时跳过）
    supersede_ids: list[str] = field(default_factory=list)


def _strip_fence(raw: str) -> str:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""     # 去掉 ```json 首行
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _parse_facts(raw: str) -> list[ExtractedFact]:
    try:
        data = json.loads(_strip_fence(raw))
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    facts: list[ExtractedFact] = []
    for item in data:
        if not isinstance(item, dict) or not item.get("text"):
            continue
        try:
            mem_type = MemType(item.get("mem_type", "semantic"))
        except ValueError:
            mem_type = MemType.SEMANTIC
        try:
            importance = float(item.get("importance", 0.5))
        except (TypeError, ValueError):
            importance = 0.5
        facts.append(ExtractedFact(
            text=str(item["text"]), mem_type=mem_type,
            entity_key=str(item.get("entity_key") or ""), importance=importance))
    return facts


class MemoryWriter:
    """LLM 驱动的智能写入：提炼 → 找候选 → 调和 → 应用。"""

    def __init__(self, backend, embedder, retriever, complete, *,
                 candidate_k: int = 5) -> None:
        self._backend = backend
        self._embedder = embedder
        self._retriever = retriever
        self._complete = complete
        self._candidate_k = candidate_k

    async def _extract(self, text: str) -> list[ExtractedFact]:
        try:
            raw = await self._complete(_EXTRACT_SYS, text)
        except Exception as e:                       # LLM 调用失败：跳过，不写
            log.warning("memory extract LLM failed: %s", e)
            return []
        return _parse_facts(raw)
```

- [ ] **步骤 4：运行验证通过**

运行：`PYTHONPATH=... pytest tests/test_memory_writer.py -q`
预期：PASS（6 passed）

- [ ] **步骤 5：Commit**

```bash
git add src/harness/memory/writer.py tests/test_memory_writer.py
git commit -m "feat(memory): writer 数据类型 + 提炼 _extract（SP3）"
```

---

## 任务 3：找候选 `_gather_candidates` + 调和 `_reconcile`

**文件：**
- 修改：`src/harness/memory/writer.py`
- 测试：`tests/test_memory_writer.py`（追加）

- [ ] **步骤 1：编写失败的测试**（追加到 `tests/test_memory_writer.py`）

```python
from harness.memory.record import MemoryFilter, MemoryRecord
from harness.memory.reranker import NoOpReranker
from harness.memory.retriever import RetrievalConfig, Retriever
from harness.memory.sqlite_backend import SqliteVecBackend
from harness.memory.writer import MemoryOp, _parse_ops


def _facts(*texts):
    return [ExtractedFact(text=t, mem_type=MemType.SEMANTIC) for t in texts]


def test_parse_ops_resolves_fact_index():
    facts = _facts("f0", "f1")
    ops = _parse_ops('[{"op":"REPLACE","fact_index":0,"supersede_ids":["x"]},'
                     '{"op":"NOOP","fact_index":1}]', facts)
    assert ops[0].op == "REPLACE" and ops[0].fact.text == "f0" and ops[0].supersede_ids == ["x"]
    assert ops[1].op == "NOOP" and ops[1].fact.text == "f1"


def test_parse_ops_uncovered_fact_defaults_add():
    facts = _facts("f0", "f1")
    ops = _parse_ops('[{"op":"NOOP","fact_index":0}]', facts)     # f1 未被决策
    adds = [o for o in ops if o.op == "ADD"]
    assert any(o.fact.text == "f1" for o in adds)                 # 补 ADD 防漏


def test_parse_ops_invalid_json_degrades_to_all_add():
    facts = _facts("f0", "f1")
    ops = _parse_ops("不是 JSON", facts)
    assert all(o.op == "ADD" for o in ops) and len(ops) == 2


async def _writer(mock_embedder, responses):
    backend = SqliteVecBackend(":memory:", dimension=64)
    emb = mock_embedder(dimension=64)
    retr = Retriever(backend, emb, NoOpReranker(), RetrievalConfig())
    return MemoryWriter(backend, emb, retr, ScriptedCompleter(responses)), backend, emb


async def test_gather_candidates_by_entity_and_semantic(mock_embedder):
    w, backend, emb = await _writer(mock_embedder, [])
    v = (await emb.embed(["用户偏好深色主题"]))[0]
    backend.upsert([MemoryRecord(owner_id="u1", kind="k", mem_type=MemType.SEMANTIC,
                                 text="用户偏好深色主题", embedding=v, id="old1",
                                 entity_key="user.pref.theme")])
    facts = [ExtractedFact(text="用户偏好深色主题", mem_type=MemType.SEMANTIC,
                           entity_key="user.pref.theme")]
    cands = await w._gather_candidates("u1", "k", facts)
    assert any(c.id == "old1" for c in cands)


async def test_reconcile_no_candidates_all_add(mock_embedder):
    w, backend, emb = await _writer(mock_embedder, [])   # 无候选时不调 LLM
    facts = _facts("f0", "f1")
    ops = await w._reconcile(facts, [])
    assert all(o.op == "ADD" for o in ops) and len(ops) == 2
```

- [ ] **步骤 2：运行验证失败**

运行：`PYTHONPATH=... pytest tests/test_memory_writer.py -q`
预期：FAIL（`_parse_ops`/`_gather_candidates`/`_reconcile` 不存在）

- [ ] **步骤 3：实现**——在 `writer.py` 加 `_RECONCILE_SYS`、`_parse_ops`（模块级），并给 `MemoryWriter` 加 `_gather_candidates`/`_reconcile` 方法：

模块级（放在 `_parse_facts` 之后）：
```python
_RECONCILE_SYS = (
    "你在维护一个记忆库。给你【新事实】列表和与之相关的【已有记忆】。"
    "对每个新事实，决定操作并输出 JSON 数组，元素："
    "{\"op\": \"ADD\"|\"NOOP\"|\"REPLACE\", \"fact_index\": 新事实下标, "
    "\"supersede_ids\": 该事实要作废的已有记忆 id 列表（仅 REPLACE）}。"
    "规则：与某条已有记忆语义等同 → NOOP；是同一实体的更新、或与某条已有记忆矛盾 → "
    "REPLACE 并在 supersede_ids 填被取代的已有记忆 id；全新信息 → ADD。只输出 JSON。"
)


def _parse_ops(raw: str, facts: list[ExtractedFact]) -> list[MemoryOp]:
    try:
        data = json.loads(_strip_fence(raw))
        if not isinstance(data, list):
            raise ValueError("not a list")
    except (json.JSONDecodeError, TypeError, ValueError):
        return [MemoryOp("ADD", f) for f in facts]        # 解析失败：全 ADD（不丢不误删）
    ops: list[MemoryOp] = []
    covered: set[int] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        op = item.get("op")
        idx = item.get("fact_index")
        if op not in ("ADD", "NOOP", "REPLACE"):
            continue
        if not isinstance(idx, int) or not (0 <= idx < len(facts)):
            continue
        covered.add(idx)
        sup = item.get("supersede_ids", []) if op == "REPLACE" else []
        if not isinstance(sup, list):
            sup = []
        ops.append(MemoryOp(op, facts[idx], [str(x) for x in sup]))
    for i, f in enumerate(facts):                          # 未被决策的事实默认 ADD
        if i not in covered:
            ops.append(MemoryOp("ADD", f))
    return ops
```

`MemoryWriter` 方法：
```python
    async def _gather_candidates(self, owner_id: str, kind: str,
                                 facts: list[ExtractedFact]) -> list[MemoryRecord]:
        from .record import MemoryFilter
        cand: dict[str, MemoryRecord] = {}
        for f in facts:
            if f.entity_key:
                for r in self._backend.list_by_entity(owner_id, kind, f.entity_key):
                    cand[r.id] = r
            hits = await self._retriever.retrieve(
                f.text, MemoryFilter(owner_id=owner_id, kind=kind), self._candidate_k)
            for h in hits:
                cand[h.record.id] = h.record
        return list(cand.values())

    async def _reconcile(self, facts: list[ExtractedFact],
                         candidates: list[MemoryRecord]) -> list[MemoryOp]:
        if not candidates:
            return [MemoryOp("ADD", f) for f in facts]     # 无候选：全 ADD，省一次 LLM
        payload = json.dumps({
            "new_facts": [{"fact_index": i, "text": f.text} for i, f in enumerate(facts)],
            "existing_memories": [{"id": c.id, "text": c.text, "entity_key": c.entity_key}
                                  for c in candidates],
        }, ensure_ascii=False)
        try:
            raw = await self._complete(_RECONCILE_SYS, payload)
        except Exception as e:
            log.warning("memory reconcile LLM failed: %s", e)
            return [MemoryOp("ADD", f) for f in facts]     # 失败降级：全 ADD
        return _parse_ops(raw, facts)
```

- [ ] **步骤 4：运行验证通过**

运行：`PYTHONPATH=... pytest tests/test_memory_writer.py -q`
预期：PASS（任务 2 的 6 + 本任务 5 = 11 passed）

- [ ] **步骤 5：Commit**

```bash
git add src/harness/memory/writer.py tests/test_memory_writer.py
git commit -m "feat(memory): 找候选 + 调和 _reconcile（SP3）"
```

---

## 任务 4：应用 `_apply` + 端到端 `write`

**文件：**
- 修改：`src/harness/memory/writer.py`
- 测试：`tests/test_memory_writer.py`（追加）

- [ ] **步骤 1：编写失败的测试**（追加）

```python
async def test_write_add_new_fact(mock_embedder):
    # 提炼出 1 条；无候选 → ADD（reconcile 不调用，只消费 1 个响应）
    w, backend, emb = await _writer(mock_embedder,
        ['[{"text":"用户在学 Rust","mem_type":"semantic","entity_key":"user.learning","importance":0.7}]'])
    ids = await w.write("u1", "k", "我在学 Rust")
    assert len(ids) == 1
    got = backend.get(ids)
    assert got[0].text == "用户在学 Rust" and got[0].mem_type == MemType.SEMANTIC
    assert got[0].importance == 0.7 and got[0].source == "extract"


async def test_write_empty_extract_noop(mock_embedder):
    w, backend, emb = await _writer(mock_embedder, ["[]"])
    assert await w.write("u1", "k", "哈哈哈") == []


async def test_write_replace_supersedes_old(mock_embedder):
    # 先放一条旧记忆；提炼新事实（同实体）；调和返回 REPLACE 作废旧的
    w, backend, emb = await _writer(mock_embedder, [
        '[{"text":"用户偏好浅色主题","mem_type":"semantic","entity_key":"user.pref.theme"}]',
        '[{"op":"REPLACE","fact_index":0,"supersede_ids":["old1"]}]',
    ])
    v = (await emb.embed(["用户偏好深色主题"]))[0]
    backend.upsert([MemoryRecord(owner_id="u1", kind="k", mem_type=MemType.SEMANTIC,
                                 text="用户偏好深色主题", embedding=v, id="old1",
                                 entity_key="user.pref.theme", version=1)])
    ids = await w.write("u1", "k", "其实我喜欢浅色主题")
    assert backend.get(["old1"])[0].superseded == 1           # 旧的作废
    new = backend.get(ids)
    assert new[0].text == "用户偏好浅色主题" and new[0].version == 2   # 新的 version+1


async def test_write_noop_dedup(mock_embedder):
    w, backend, emb = await _writer(mock_embedder, [
        '[{"text":"用户偏好深色主题","mem_type":"semantic","entity_key":"user.pref.theme"}]',
        '[{"op":"NOOP","fact_index":0}]',
    ])
    v = (await emb.embed(["用户偏好深色主题"]))[0]
    backend.upsert([MemoryRecord(owner_id="u1", kind="k", mem_type=MemType.SEMANTIC,
                                 text="用户偏好深色主题", embedding=v, id="old1",
                                 entity_key="user.pref.theme")])
    ids = await w.write("u1", "k", "深色主题真好")
    assert ids == []                                          # 去重：不新增
    assert backend.get(["old1"])[0].superseded == 0           # 旧的不动
```

- [ ] **步骤 2：运行验证失败**

运行：`PYTHONPATH=... pytest tests/test_memory_writer.py -q`
预期：FAIL（`write`/`_apply` 不存在）

- [ ] **步骤 3：实现**——给 `MemoryWriter` 加 `_apply` 与 `write`：

```python
    async def _apply(self, owner_id: str, kind: str,
                     ops: list[MemoryOp]) -> list[str]:
        new_ids: list[str] = []
        for op in ops:
            if op.op == "NOOP" or op.fact is None:
                continue
            version = 1
            if op.op == "REPLACE" and op.supersede_ids:
                old = self._backend.get(op.supersede_ids)
                version = max([r.version for r in old], default=0) + 1
                self._backend.set_superseded(op.supersede_ids)
            vec = (await self._embedder.embed([op.fact.text]))[0]
            rec = MemoryRecord(
                owner_id=owner_id, kind=kind, mem_type=op.fact.mem_type,
                text=op.fact.text, embedding=vec, entity_key=op.fact.entity_key,
                importance=op.fact.importance, version=version, source="extract")
            new_ids.extend(self._backend.upsert([rec]))
        return new_ids

    async def write(self, owner_id: str, kind: str, text: str) -> list[str]:
        """智能写入：提炼→找候选→调和→应用，返回新写入记录 id。best-effort，异常安全。"""
        if not text or not text.strip():
            return []
        facts = await self._extract(text)
        if not facts:
            return []
        candidates = await self._gather_candidates(owner_id, kind, facts)
        ops = await self._reconcile(facts, candidates)
        return await self._apply(owner_id, kind, ops)
```

- [ ] **步骤 4：运行验证通过**

运行：`PYTHONPATH=... pytest tests/test_memory_writer.py -q`
预期：PASS（11 + 4 = 15 passed）

- [ ] **步骤 5：Commit**

```bash
git add src/harness/memory/writer.py tests/test_memory_writer.py
git commit -m "feat(memory): _apply + 端到端 write（SP3）"
```

---

## 任务 5：ConversationMemoryService 集成（config 门控）

**文件：**
- 修改：`app/conversation_memory.py`
- 测试：`tests/app/test_conversation_memory.py`（追加）；现有用例应继续绿

> `record_turn` 增加：若注入了 `writer`（启用智能写入）→ 走 `writer.write`；否则走原 `add_texts` 原文入库（SP1 行为）。`sample_rate<1.0` 时按 text 的确定性哈希采样降频（控成本，测试可复现）。

- [ ] **步骤 1：编写失败的测试**（追加到 `tests/app/test_conversation_memory.py`）

```python
class _FakeWriter:
    def __init__(self):
        self.calls = []
    async def write(self, owner_id, kind, text):
        self.calls.append((owner_id, kind, text))
        return ["new1"]


async def test_record_turn_uses_writer_when_present(mock_embedder):
    from app.conversation_memory import ConversationMemoryService
    from harness.memory.memory import Memory
    from harness.memory.sqlite_backend import SqliteVecBackend
    mem = Memory(SqliteVecBackend(":memory:", dimension=64), mock_embedder(dimension=64))
    fw = _FakeWriter()
    svc = ConversationMemoryService(mem, writer=fw)
    out = await svc.record_turn("c1", 0, "我在学 Python")
    assert out == ["new1"]
    assert fw.calls == [("c1", "conversation", "我在学 Python")]     # owner=conv_id, kind=前缀


async def test_record_turn_default_raw_when_no_writer(mock_embedder):
    # writer=None → 原文入库（SP1 行为），能被检索回
    from app.conversation_memory import ConversationMemoryService
    from harness.memory.memory import Memory
    from harness.memory.sqlite_backend import SqliteVecBackend
    mem = Memory(SqliteVecBackend(":memory:", dimension=64), mock_embedder(dimension=64))
    svc = ConversationMemoryService(mem)
    await svc.record_turn("c1", 0, "快速排序是一种排序算法")
    hits = await svc.retrieve("c1", "排序算法", k=1)
    assert hits and hits[0].text == "快速排序是一种排序算法"
```

- [ ] **步骤 2：运行验证失败**

运行：`PYTHONPATH=... pytest tests/app/test_conversation_memory.py -q`
预期：FAIL（`ConversationMemoryService` 不接受 `writer` 参数 → TypeError）

- [ ] **步骤 3：实现**——改 `app/conversation_memory.py`：

`__init__` 增加参数：
```python
    def __init__(self, memory, collection_prefix: str = "conversation",
                 writer=None, sample_rate: float = 1.0) -> None:
        self._memory = memory
        self._prefix = collection_prefix
        self._writer = writer
        self._sample_rate = sample_rate
```

`record_turn` 改为：
```python
    async def record_turn(self, conv_id: str, seq: int, text: str) -> list[str]:
        """轮结束异步写入。启用 writer 时走智能写入（提炼/去重/矛盾），否则原文入库。"""
        if not text or not text.strip():
            return []
        if self._writer is not None and self._should_sample(text):
            return await self._writer.write(conv_id, self._prefix, text)
        return await self._memory.add_texts(
            [text], self._collection_for(conv_id), {"seq": seq})

    def _should_sample(self, text: str) -> bool:
        if self._sample_rate >= 1.0:
            return True
        if self._sample_rate <= 0.0:
            return False
        import hashlib
        h = int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16) % 1000
        return h < self._sample_rate * 1000
```

- [ ] **步骤 4：运行验证通过**

运行：`PYTHONPATH=... pytest tests/app/test_conversation_memory.py -q`
预期：PASS（新 2 + 现有全绿）

- [ ] **步骤 5：Commit**

```bash
git add app/conversation_memory.py tests/app/test_conversation_memory.py
git commit -m "feat(memory): ConversationMemoryService 集成智能写入（SP3）"
```

---

## 任务 6：配置 + assembly/chat 注入 + 全仓回归

**文件：**
- 修改：`app/config.py`、`app/assembly.py`、`app/api/chat.py`
- 测试：全仓回归

> `MemoryWriter` 在 assembly 内构造（config 启用时）并挂到 `Harness`；`app/api/chat.py:95` 的 `ConversationMemoryService(harness.memory)` 改为传入 `writer` 与 `sample_rate`。

- [ ] **步骤 1：先跑基线**

运行：`PYTHONPATH=... pytest tests/app/test_conversation_memory.py tests/app/test_api.py tests/app/test_assembly.py tests/app/test_chat_gate.py -q`
记录当前 PASS 数（注入后须仍绿）。

- [ ] **步骤 2：加配置**——`app/config.py` 在 attachment 配置附近加：
```python
    memory_write_extract: bool = False
    memory_write_sample_rate: float = 1.0
    memory_write_candidate_k: int = 5
```

- [ ] **步骤 3：assembly 构造 writer**。`app/assembly.py` 结构：顶层 `client`（约第 35 行，`RetryingModelClient`）始终可用；记忆装配（`embedder`/`mem_store`/`_retriever`）在 `if config.api_key or config.embedding_api_key:` 块内（约 80-100 行）；`Harness` dataclass 在第 18 行、`return Harness(...)` 在第 181 行。

(a) 在记忆装配块内、`_retriever = Retriever(...)` 与 `mem = Memory(...)` 之后加（此处 `client`/`embedder`/`mem_store`/`_retriever` 均在作用域内）：
```python
        memory_writer = None
        if config.memory_write_extract:
            from harness.memory.writer import MemoryWriter
            from app.completion import build_completer
            memory_writer = MemoryWriter(
                mem_store, embedder, _retriever,
                build_completer(client, config.model),
                candidate_k=config.memory_write_candidate_k)
```
注意：`memory_writer` 需在 `if config.api_key...` 块**之前**先初始化为 `None`（与 `memory`/`memory_store` 同样的模式，见文件顶部它们的 `= None` 初始化），避免块未进入时未定义。

(b) `Harness` dataclass（第 18 行处）加字段：`memory_writer: object | None = None`。

(c) `return Harness(...)`（第 181 行）加传参：`memory_writer=memory_writer`。

- [ ] **步骤 4：chat 注入**——`app/api/chat.py:95` 附近：
```python
            _conv_memory = ConversationMemoryService(
                harness.memory,
                writer=getattr(harness, "memory_writer", None),
                sample_rate=config.memory_write_sample_rate)
```
（该处 `config` 变量在 `make_chat_router` 作用域内可得；若变量名不同以实际为准。）

- [ ] **步骤 5：跑相关 + 全仓回归**

相关：`PYTHONPATH=... pytest tests/app/test_conversation_memory.py tests/app/test_api.py tests/app/test_assembly.py tests/app/test_chat_gate.py tests/test_memory_writer.py tests/test_backend_superseded.py -q`（预期与基线一致 + SP3 全绿）

全仓（排除外部依赖）：
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
预期：仅既有 2 个无关 sandbox 失败，其余全绿。

- [ ] **步骤 6：Commit**

```bash
git add app/config.py app/assembly.py app/api/chat.py
git commit -m "feat(memory): 写入配置 + assembly/chat 注入 MemoryWriter（SP3）"
```

---

## 自检结论

- **规格覆盖**：set_superseded（T1）、数据类型+提炼（T2）、找候选+调和（T3）、应用+write（T4）、集成门控（T5）、配置+注入（T6）——规格 §1 IN 全覆盖；§1 OUT（TTL/consolidation/评测扩展）不实现。
- **类型一致**：`ExtractedFact`/`MemoryOp`/`MemoryWriter`（writer.py）贯穿 T2-T5；`MemoryOp.fact` 对 ADD/NOOP/REPLACE 均由 fact_index 解析填充（NOOP 应用时跳过）；`set_superseded` 签名在 backend.py(T1)/sqlite_backend.py(T1)/_apply(T4) 一致；`MemoryWriter.__init__(backend, embedder, retriever, complete, *, candidate_k)` 在 T2 定义、T4/T6 一致引用。
- **向后兼容**：`ConversationMemoryService` 的 `writer` 默认 None → 走原 `add_texts`，SP1/SP2 行为与现有测试不变；config `memory_write_extract` 默认 False。
- **无占位符**：每步含可运行代码与命令；解析失败降级/采样是规格明确行为。T6 步骤 3 对 client 变量名留了「以实际为准」的核对指引（assembly 里 client 的确切持有方式需实现者读一眼），这是集成点的合理核对而非占位。
- **worktree**：所有命令带 `PYTHONPATH="$PWD/src:$PWD"`。

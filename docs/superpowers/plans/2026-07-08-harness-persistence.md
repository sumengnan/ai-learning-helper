# AI Harness 持久化（子项目③d）实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 轨迹 sink（事件流落 SQLite）+ checkpoint 断点续跑（RunState 序列化 + `AgentLoop.resume`）。

**架构：** `persistence/`（serialize + TrajectoryStore/Sink + CheckpointStore）；loop 抽 `_run_from` 供 run/resume 共用、步边界 checkpoint（fresh run 行为零变化）。

**技术栈：** 沿用 · **无新依赖**（标准库 `sqlite3`+`json`）。

**规格：** `docs/superpowers/specs/2026-07-08-harness-persistence-design.md`

**提交规范：** git 身份已是 sumengnan，默认提交；**任何 commit message 不得出现 Claude/AI/Co-Authored-By 等署名**。

---

## 文件结构与职责

| 文件 | 动作 | 职责 |
|---|---|---|
| `src/harness/persistence/serialize.py` | 新增 | RunState/Message/ToolCall ↔ dict；event_to_dict |
| `src/harness/persistence/trajectory.py` | 新增 | `TrajectoryStore` + `TrajectorySink` |
| `src/harness/persistence/checkpoint.py` | 新增 | `CheckpointStore` |
| `src/harness/loop/agent_loop.py` | 改（整体替换） | 抽 `_run_from`；加 `resume`、`checkpoint_store`；步边界 checkpoint |
| `src/harness/config.py` | 改 | `persistence_db_path` |

**依赖方向**：`persistence → {types, state, events}`；`loop → persistence`（仅类型注解/可选注入，无环——persistence 不 import loop）。

---

## 任务 0：配置

**文件：** 改 `src/harness/config.py`、测试 `tests/test_config.py`

- [ ] **步骤 1：写失败测试**（追加）

```python
def test_persistence_defaults():
    cfg = HarnessConfig(api_key="k")
    assert cfg.persistence_db_path == "harness.db"
```

运行：`uv run pytest tests/test_config.py::test_persistence_defaults -v`　预期 FAIL。

- [ ] **步骤 2：改 `config.py`** 末尾追加：

```python
    # 持久化
    persistence_db_path: str = "harness.db"
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/test_config.py -v`　预期全 pass。
```bash
git add src/harness/config.py tests/test_config.py
git commit -m "chore: 持久化配置项"
```

---

## 任务 1：序列化 `persistence/serialize.py`

**文件：** 创建 `src/harness/persistence/__init__.py`、`serialize.py`、测试 `tests/test_serialize.py`

- [ ] **步骤 1：建包**　运行：`touch src/harness/persistence/__init__.py`

- [ ] **步骤 2：写失败测试**

```python
# tests/test_serialize.py
from harness.persistence.serialize import (
    message_to_dict, message_from_dict, runstate_to_dict, runstate_from_dict, event_to_dict)
from harness.types import Message, Role, ToolCall, ToolResult
from harness.state import RunState
from harness.events import RunFinished, TextDelta, ToolFinished, ModelUsage
from harness.usage import Usage


def test_message_roundtrip():
    m = Message(role=Role.ASSISTANT, content=None,
                tool_calls=[ToolCall(id="c1", name="calc", arguments={"x": 1})])
    m2 = message_from_dict(message_to_dict(m))
    assert m2.role == Role.ASSISTANT and m2.content is None
    assert m2.tool_calls[0].id == "c1" and m2.tool_calls[0].arguments == {"x": 1}


def test_runstate_roundtrip():
    st = RunState(run_id="r1"); st.step = 2
    st.append(Message(role=Role.USER, content="hi"))
    st.append(Message(role=Role.ASSISTANT, content=None,
                      tool_calls=[ToolCall(id="c1", name="t", arguments={})]))
    st.append(Message(role=Role.TOOL, content="42", tool_call_id="c1"))
    st2 = runstate_from_dict(runstate_to_dict(st))
    assert st2.run_id == "r1" and st2.step == 2
    assert [m.role for m in st2.messages] == [Role.USER, Role.ASSISTANT, Role.TOOL]
    assert st2.messages[1].tool_calls[0].id == "c1"
    assert st2.messages[2].tool_call_id == "c1"


def test_event_to_dict_variants():
    assert event_to_dict(TextDelta(text="hi")) == {"type": "TextDelta", "data": {"text": "hi"}}
    tf = event_to_dict(ToolFinished(result=ToolResult("c1", "ok", False)))
    assert tf["type"] == "ToolFinished" and tf["data"]["result"]["content"] == "ok"
    rf = event_to_dict(RunFinished(message=Message(role=Role.ASSISTANT, content="done")))
    assert rf["data"]["message"]["content"] == "done"
    mu = event_to_dict(ModelUsage(usage=Usage(1, 2, 3), cost_usd=0.5, attempts=1, latency_ms=10.0))
    assert mu["data"]["usage"]["total"] == 3 and mu["data"]["cost_usd"] == 0.5
```

运行：预期 FAIL。

- [ ] **步骤 3：实现 `src/harness/persistence/serialize.py`**

```python
# src/harness/persistence/serialize.py
from __future__ import annotations

from ..events import (
    ModelUsage, RunError, RunFinished, RunStarted, StepFinished, StepStarted,
    TextDelta, ToolCallRequested, ToolFinished, ToolStarted,
)
from ..state import RunState
from ..types import Message, Role, ToolCall


def toolcall_to_dict(tc: ToolCall) -> dict:
    return {"id": tc.id, "name": tc.name, "arguments": tc.arguments}


def toolcall_from_dict(d: dict) -> ToolCall:
    return ToolCall(id=d["id"], name=d["name"], arguments=d["arguments"])


def message_to_dict(m: Message) -> dict:
    return {
        "role": m.role.value,
        "content": m.content,
        "tool_calls": [toolcall_to_dict(tc) for tc in m.tool_calls],
        "tool_call_id": m.tool_call_id,
    }


def message_from_dict(d: dict) -> Message:
    return Message(
        role=Role(d["role"]),
        content=d.get("content"),
        tool_calls=[toolcall_from_dict(x) for x in d.get("tool_calls", [])],
        tool_call_id=d.get("tool_call_id"),
    )


def runstate_to_dict(s: RunState) -> dict:
    return {"run_id": s.run_id, "step": s.step,
            "messages": [message_to_dict(m) for m in s.messages]}


def runstate_from_dict(d: dict) -> RunState:
    st = RunState(run_id=d["run_id"])
    st.step = d["step"]
    st.messages = [message_from_dict(m) for m in d["messages"]]
    return st


def event_to_dict(ev) -> dict:
    """单向：把事件序列化成 {type, data} 供轨迹存储/阅读。"""
    if isinstance(ev, RunStarted):
        data = {"run_id": ev.run_id}
    elif isinstance(ev, (StepStarted, StepFinished)):
        data = {"step": ev.step}
    elif isinstance(ev, TextDelta):
        data = {"text": ev.text}
    elif isinstance(ev, ToolCallRequested):
        data = {"tool_calls": [toolcall_to_dict(tc) for tc in ev.tool_calls]}
    elif isinstance(ev, ToolStarted):
        data = {"tool_call": toolcall_to_dict(ev.tool_call)}
    elif isinstance(ev, ToolFinished):
        r = ev.result
        data = {"result": {"tool_call_id": r.tool_call_id, "content": r.content, "is_error": r.is_error}}
    elif isinstance(ev, RunFinished):
        data = {"message": message_to_dict(ev.message)}
    elif isinstance(ev, RunError):
        data = {"error": ev.error}
    elif isinstance(ev, ModelUsage):
        u = ev.usage
        data = {"usage": {"prompt": u.prompt_tokens, "completion": u.completion_tokens, "total": u.total_tokens},
                "cost_usd": ev.cost_usd, "attempts": ev.attempts, "latency_ms": ev.latency_ms}
    else:
        data = {}
    return {"type": type(ev).__name__, "data": data}
```

- [ ] **步骤 4：跑通并 commit**

运行：`uv run pytest tests/test_serialize.py -v`　预期：3 passed。
```bash
git add src/harness/persistence/__init__.py src/harness/persistence/serialize.py tests/test_serialize.py
git commit -m "feat: 持久化序列化 RunState/Message/Event"
```

---

## 任务 2：轨迹 sink `persistence/trajectory.py`

**文件：** 创建 `src/harness/persistence/trajectory.py`、测试 `tests/test_trajectory.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/test_trajectory.py
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink
from harness.events import RunStarted, TextDelta, RunFinished
from harness.types import Message, Role


async def _gen(evs):
    for e in evs:
        yield e


def test_store_append_load_ordered():
    s = TrajectoryStore(":memory:")
    s.append("r1", 0, {"type": "A", "data": {}})
    s.append("r1", 1, {"type": "B", "data": {}})
    assert [e["type"] for e in s.load("r1")] == ["A", "B"]


async def test_sink_records_and_passes_through():
    store = TrajectoryStore(":memory:")
    sink = TrajectorySink(store)
    evs = [RunStarted(run_id="r1"), TextDelta(text="hi"),
           RunFinished(message=Message(role=Role.ASSISTANT, content="done"))]
    out = [e async for e in sink.wrap(_gen(evs))]
    assert len(out) == 3                               # 透传
    assert [e["type"] for e in store.load("r1")] == ["RunStarted", "TextDelta", "RunFinished"]
```

运行：预期 FAIL。

- [ ] **步骤 2：实现 `src/harness/persistence/trajectory.py`**

```python
# src/harness/persistence/trajectory.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from ..events import RunStarted
from .serialize import event_to_dict


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TrajectoryStore:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS trajectory_events("
            "run_id TEXT, seq INTEGER, type TEXT, data TEXT, created_at TEXT, "
            "PRIMARY KEY(run_id, seq))")
        self._conn.commit()

    def append(self, run_id: str, seq: int, event_dict: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO trajectory_events(run_id, seq, type, data, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, seq, event_dict.get("type", ""),
             json.dumps(event_dict, ensure_ascii=False), _now()))
        self._conn.commit()

    def load(self, run_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT data FROM trajectory_events WHERE run_id = ? ORDER BY seq",
            (run_id,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def close(self) -> None:
        self._conn.close()


class TrajectorySink:
    """事件流包装器：透传事件，同时按序落库。run_id 从 RunStarted 捕获。"""

    def __init__(self, store: TrajectoryStore) -> None:
        self._store = store

    async def wrap(self, events):
        run_id, seq = None, 0
        async for ev in events:
            if isinstance(ev, RunStarted):
                run_id = ev.run_id
            if run_id is not None:
                self._store.append(run_id, seq, event_to_dict(ev))
                seq += 1
            yield ev
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/test_trajectory.py -v`　预期：2 passed。
```bash
git add src/harness/persistence/trajectory.py tests/test_trajectory.py
git commit -m "feat: TrajectoryStore + TrajectorySink 轨迹落库"
```

---

## 任务 3：checkpoint 存储 `persistence/checkpoint.py`

**文件：** 创建 `src/harness/persistence/checkpoint.py`、测试 `tests/test_checkpoint.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/test_checkpoint.py
from harness.persistence.checkpoint import CheckpointStore
from harness.state import RunState
from harness.types import Message, Role


def test_save_load_roundtrip():
    cs = CheckpointStore(":memory:")
    st = RunState(run_id="r1"); st.step = 3
    st.append(Message(role=Role.USER, content="hi"))
    cs.save(st)
    loaded = cs.load("r1")
    assert loaded.run_id == "r1" and loaded.step == 3 and loaded.messages[0].content == "hi"


def test_load_missing_returns_none():
    assert CheckpointStore(":memory:").load("nope") is None


def test_delete():
    cs = CheckpointStore(":memory:")
    cs.save(RunState(run_id="r1"))
    cs.delete("r1")
    assert cs.load("r1") is None
```

运行：预期 FAIL。

- [ ] **步骤 2：实现 `src/harness/persistence/checkpoint.py`**

```python
# src/harness/persistence/checkpoint.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from ..state import RunState
from .serialize import runstate_from_dict, runstate_to_dict


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CheckpointStore:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS checkpoints("
            "run_id TEXT PRIMARY KEY, state TEXT, step INTEGER, updated_at TEXT)")
        self._conn.commit()

    def save(self, state: RunState) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO checkpoints(run_id, state, step, updated_at) VALUES (?, ?, ?, ?)",
            (state.run_id, json.dumps(runstate_to_dict(state), ensure_ascii=False), state.step, _now()))
        self._conn.commit()

    def load(self, run_id: str) -> RunState | None:
        row = self._conn.execute(
            "SELECT state FROM checkpoints WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return runstate_from_dict(json.loads(row[0]))

    def delete(self, run_id: str) -> None:
        self._conn.execute("DELETE FROM checkpoints WHERE run_id = ?", (run_id,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/test_checkpoint.py -v`　预期：3 passed。
```bash
git add src/harness/persistence/checkpoint.py tests/test_checkpoint.py
git commit -m "feat: CheckpointStore RunState 快照存取"
```

---

## 任务 4：loop 改动（抽 `_run_from` + resume + checkpoint）

**文件：** 整体替换 `src/harness/loop/agent_loop.py`、测试 `tests/test_loop_checkpoint.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/test_loop_checkpoint.py
from harness.loop.agent_loop import AgentLoop
from harness.context.manager import ContextManager
from harness.tools.base import ToolRegistry
from harness.tools.builtins.calculator import CalculatorTool
from harness.persistence.checkpoint import CheckpointStore
from harness.state import RunState
from harness.types import Message, Role, ToolCall
from harness.llm.base import StreamChunk, ToolCallDelta
from harness.events import RunFinished, RunError, StepStarted, RunStarted


class _RecordingStore:
    def __init__(self): self.saves = []; self.deleted = []
    def save(self, state): self.saves.append(state.step)
    def load(self, run_id): return None
    def delete(self, run_id): self.deleted.append(run_id)


def _reg():
    r = ToolRegistry(); r.register(CalculatorTool()); return r


async def test_checkpoint_saved_each_step_deleted_on_finish(make_mock, text_turn):
    store = _RecordingStore()
    tool_turn = [StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
        index=0, id="c1", name="calculator", arguments='{"expression":"1+1"}')),
        StreamChunk(type="done")]
    loop = AgentLoop(client=make_mock([tool_turn, text_turn("答案 2")]),
                     registry=_reg(), context=ContextManager("s"), max_steps=5,
                     run_id_factory=lambda: "r1", checkpoint_store=store)
    _ = [e async for e in loop.run("算 1+1")]
    assert store.saves == [1]              # step1 结束存一次
    assert store.deleted == ["r1"]         # RunFinished 删


async def test_backward_compat_no_store_same_events(make_mock, text_turn):
    loop = AgentLoop(client=make_mock([text_turn("hi")]), registry=ToolRegistry(),
                     context=ContextManager("s"), max_steps=5, run_id_factory=lambda: "r1")
    events = [e async for e in loop.run("hi")]
    assert isinstance(events[0], RunStarted) and isinstance(events[-1], RunFinished)


async def test_resume_continues_from_checkpoint(make_mock, text_turn):
    cs = CheckpointStore(":memory:")
    st = RunState(run_id="r1"); st.step = 1
    st.append(Message(role=Role.USER, content="算 1+1"))
    st.append(Message(role=Role.ASSISTANT, content=None,
                      tool_calls=[ToolCall(id="c1", name="calculator", arguments={"expression": "1+1"})]))
    st.append(Message(role=Role.TOOL, content="2", tool_call_id="c1"))
    cs.save(st)

    loop = AgentLoop(client=make_mock([text_turn("最终答案 2")]),
                     registry=_reg(), context=ContextManager("s"), max_steps=5,
                     checkpoint_store=cs)
    events = [e async for e in loop.resume("r1")]
    assert not any(isinstance(e, RunStarted) for e in events)          # resume 不再发 RunStarted
    assert [e.step for e in events if isinstance(e, StepStarted)] == [2]  # 从 step2 续
    assert isinstance(events[-1], RunFinished)
    assert "最终答案 2" in events[-1].message.content
    assert cs.load("r1") is None                                       # 完成后快照删


async def test_resume_missing_checkpoint_run_error(make_mock, text_turn):
    loop = AgentLoop(client=make_mock([text_turn("x")]), registry=ToolRegistry(),
                     context=ContextManager("s"), checkpoint_store=CheckpointStore(":memory:"))
    events = [e async for e in loop.resume("nope")]
    assert isinstance(events[-1], RunError) and "无 checkpoint" in events[-1].error
```

运行：预期 FAIL（`resume`/`checkpoint_store` 不存在）。

- [ ] **步骤 2：整体替换 `src/harness/loop/agent_loop.py`**

```python
# src/harness/loop/agent_loop.py
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import AsyncIterator, Callable

from opentelemetry.trace import Status, StatusCode

from ..context.manager import ContextManager
from ..events import (
    Event, RunStarted, StepStarted, TextDelta, ToolCallRequested,
    ToolStarted, ToolFinished, StepFinished, RunFinished, RunError, ModelUsage,
)
from ..llm.base import ModelClient, ToolCallDelta
from ..reliability.budget import BudgetTracker, BudgetExceeded
from ..state import RunState
from ..telemetry.tracer import get_tracer
from ..tools.base import ToolExecutor, ToolRegistry
from ..types import Message, Role, ToolCall, ToolResult
from ..usage import cost_usd


@dataclass
class _Finalized:
    call: ToolCall
    parse_error: str | None


def _accumulate(acc: dict[int, dict], delta: ToolCallDelta) -> None:
    slot = acc.setdefault(delta.index, {"id": None, "name": None, "args": ""})
    if delta.id:
        slot["id"] = delta.id
    if delta.name:
        slot["name"] = delta.name
    if delta.arguments:
        slot["args"] += delta.arguments


def _finalize(acc: dict[int, dict]) -> list[_Finalized]:
    out: list[_Finalized] = []
    for idx in sorted(acc):
        slot = acc[idx]
        parse_error: str | None = None
        args: dict = {}
        if slot["args"]:
            try:
                parsed = json.loads(slot["args"])
                if isinstance(parsed, dict):
                    args = parsed
                else:
                    parse_error = f"参数需为 JSON 对象，收到：{slot['args'][:80]}"
            except json.JSONDecodeError as e:
                parse_error = f"{e}：{slot['args'][:80]}"
        out.append(_Finalized(
            call=ToolCall(id=slot["id"] or f"call_{idx}", name=slot["name"] or "", arguments=args),
            parse_error=parse_error,
        ))
    return out


class AgentLoop:
    def __init__(
        self,
        client: ModelClient,
        registry: ToolRegistry,
        context: ContextManager,
        max_steps: int = 10,
        run_id_factory: Callable[[], str] | None = None,
        budget: BudgetTracker | None = None,
        tracer=None,
        model_name: str = "",
        price_map: dict | None = None,
        tool_result_max_chars: int | None = None,
        checkpoint_store=None,
    ) -> None:
        self._client = client
        self._registry = registry
        self._executor = ToolExecutor(registry, max_chars=tool_result_max_chars)
        self._context = context
        self._max_steps = max_steps
        self._new_run_id = run_id_factory or (lambda: uuid.uuid4().hex)
        self._budget = budget
        self._tracer = tracer or get_tracer()
        self._model_name = model_name
        self._price_map = price_map or {}
        self._checkpoint_store = checkpoint_store

    async def run(self, user_message: str) -> AsyncIterator[Event]:
        state = RunState(run_id=self._new_run_id())
        state.append(Message(role=Role.USER, content=user_message))
        async for ev in self._run_from(state, resuming=False):
            yield ev

    async def resume(self, run_id: str) -> AsyncIterator[Event]:
        if self._checkpoint_store is None:
            yield RunError(error="未配置 checkpoint_store，无法 resume")
            return
        state = self._checkpoint_store.load(run_id)
        if state is None:
            yield RunError(error=f"无 checkpoint：{run_id}")
            return
        async for ev in self._run_from(state, resuming=True):
            yield ev

    async def _run_from(self, state: RunState, resuming: bool) -> AsyncIterator[Event]:
        if self._budget:
            self._budget.start()
        if not resuming:
            yield RunStarted(run_id=state.run_id)

        with self._tracer.start_as_current_span("run") as run_span:
            run_span.set_attribute("harness.run_id", state.run_id)

            for step in range(state.step + 1, self._max_steps + 1):
                state.step = step

                if self._budget:  # 步边界预算检查
                    try:
                        self._budget.check()
                    except BudgetExceeded as e:
                        run_span.set_status(Status(StatusCode.ERROR, e.reason))
                        yield RunError(error=e.reason)
                        return

                yield StepStarted(step=step)

                with self._tracer.start_as_current_span("step") as step_span:
                    step_span.set_attribute("harness.step", step)

                    messages = self._context.build(state)
                    content_parts: list[str] = []
                    tool_acc: dict[int, dict] = {}
                    usage = None
                    attempts = 1
                    t0 = time.monotonic()
                    try:
                        with self._tracer.start_as_current_span("model_call") as mc_span:
                            mc_span.set_attribute("harness.model", self._model_name)
                            async for chunk in self._client.stream(messages, self._registry.schemas()):
                                if chunk.type == "text" and chunk.text:
                                    content_parts.append(chunk.text)
                                    yield TextDelta(text=chunk.text)
                                elif chunk.type == "tool_call" and chunk.tool_call_delta:
                                    _accumulate(tool_acc, chunk.tool_call_delta)
                                elif chunk.type == "done":
                                    usage = chunk.usage
                                    attempts = chunk.attempts
                            if usage is not None:
                                mc_span.set_attribute("harness.tokens.total", usage.total_tokens)
                            mc_span.set_attribute("harness.attempts", attempts)
                    except Exception as e:
                        step_span.set_status(Status(StatusCode.ERROR, str(e)))
                        yield RunError(error=f"模型调用失败: {e}")
                        return

                    latency_ms = (time.monotonic() - t0) * 1000
                    if usage is not None:
                        cost = cost_usd(usage, self._model_name, self._price_map)
                        if self._budget:
                            self._budget.add_usage(usage)
                        yield ModelUsage(usage=usage, cost_usd=cost, attempts=attempts, latency_ms=latency_ms)

                    finalized = _finalize(tool_acc)
                    tool_calls = [f.call for f in finalized]
                    assistant = Message(
                        role=Role.ASSISTANT,
                        content="".join(content_parts) or None,
                        tool_calls=tool_calls,
                    )
                    state.append(assistant)

                    if not tool_calls:
                        yield RunFinished(message=assistant)
                        if self._checkpoint_store:
                            self._checkpoint_store.delete(state.run_id)
                        return

                    yield ToolCallRequested(tool_calls=tool_calls)
                    for f in finalized:
                        tc = f.call
                        yield ToolStarted(tool_call=tc)
                        with self._tracer.start_as_current_span(f"tool_call:{tc.name}") as ts:
                            if f.parse_error:  # 自纠正：回填明确错误，让模型下一步重发
                                result = ToolResult(
                                    tc.id,
                                    f"工具调用参数不是合法 JSON：{f.parse_error}，请重新调用。",
                                    is_error=True,
                                )
                            else:
                                result = await self._executor.execute(tc)
                            ts.set_attribute("harness.tool.is_error", result.is_error)
                            if result.is_error:
                                ts.set_status(Status(StatusCode.ERROR, result.content[:200]))
                                ts.add_event("tool.error", {"content": result.content[:200]})
                        state.append(Message(role=Role.TOOL, content=result.content, tool_call_id=tc.id))
                        yield ToolFinished(result=result)
                    yield StepFinished(step=step)
                    if self._checkpoint_store:  # 步边界存快照
                        self._checkpoint_store.save(state)

            yield RunError(error=f"达到 max_steps 上限 ({self._max_steps})")
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/test_loop_checkpoint.py tests/test_loop.py -v`　预期：新测试 + ①原有 loop 测试全 pass（向后兼容）。再 `uv run pytest -q` 确认全项目无回归。
```bash
git add src/harness/loop/agent_loop.py tests/test_loop_checkpoint.py
git commit -m "feat: AgentLoop 抽 _run_from + resume + 步边界 checkpoint"
```

---

## 任务 5：端到端集成 + demo

**文件：** 测试 `tests/test_persistence_integration.py`、新增 `examples/persistence_demo.py`

- [ ] **步骤 1：写集成测试**（真 loop 被 TrajectorySink 包裹 → 完整轨迹落库）

```python
# tests/test_persistence_integration.py
from harness.loop.agent_loop import AgentLoop
from harness.context.manager import ContextManager
from harness.tools.base import ToolRegistry
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink


async def test_real_loop_trajectory_persisted(make_mock, text_turn):
    store = TrajectoryStore(":memory:")
    sink = TrajectorySink(store)
    loop = AgentLoop(client=make_mock([text_turn("你好")]), registry=ToolRegistry(),
                     context=ContextManager("s"), max_steps=5, run_id_factory=lambda: "r1")
    _ = [e async for e in sink.wrap(loop.run("hi"))]
    types = [e["type"] for e in store.load("r1")]
    assert types[0] == "RunStarted"
    assert types[-1] == "RunFinished"
    assert "TextDelta" in types
```

运行：`uv run pytest tests/test_persistence_integration.py -v`　预期：1 passed。

- [ ] **步骤 2：新增 `examples/persistence_demo.py`**

```python
# examples/persistence_demo.py
"""持久化手动验收：一次 run 的轨迹落 SQLite，并演示 checkpoint。
需要 .env 配好聊天端点。

运行：uv run python examples/persistence_demo.py "帮我算 (12+8)*3"
"""
from __future__ import annotations

import asyncio
import sys

from harness.config import HarnessConfig
from harness.context.manager import ContextManager
from harness.llm.openai_compat import OpenAICompatibleClient
from harness.loop.agent_loop import AgentLoop
from harness.tools.base import ToolRegistry
from harness.tools.builtins.calculator import CalculatorTool
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink
from harness.persistence.checkpoint import CheckpointStore
from harness.events import TextDelta, RunFinished, RunError


async def main(msg: str) -> None:
    cfg = HarnessConfig()
    traj = TrajectoryStore(cfg.persistence_db_path)
    ckpt = CheckpointStore(cfg.persistence_db_path)
    sink = TrajectorySink(traj)

    reg = ToolRegistry(); reg.register(CalculatorTool())
    loop = AgentLoop(client=OpenAICompatibleClient(cfg), registry=reg,
                     context=ContextManager("你可以用 calculator 计算。"),
                     max_steps=cfg.max_steps, model_name=cfg.model, checkpoint_store=ckpt)

    run_id = None
    async for ev in sink.wrap(loop.run(msg)):
        if isinstance(ev, TextDelta):
            print(ev.text, end="", flush=True)
        elif isinstance(ev, RunFinished):
            print("\n[完成]")
        elif isinstance(ev, RunError):
            print(f"\n[出错] {ev.error}")

    # 展示落库的轨迹（取任一已存 run 的事件类型序列）
    rows = traj._conn.execute("SELECT DISTINCT run_id FROM trajectory_events").fetchall()
    if rows:
        rid = rows[-1][0]
        print(f"\n=== 轨迹已落库 run_id={rid} ===")
        for e in traj.load(rid):
            print(" ", e["type"])


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "帮我算 (12+8)*3"))
```

- [ ] **步骤 3：全量测试并 commit**

运行：`uv run pytest -q`　预期：全绿（既有 3 个 skip 不变）。
```bash
git add tests/test_persistence_integration.py examples/persistence_demo.py
git commit -m "feat: 持久化端到端集成测试 + demo"
```

---

## 完成标准（对照规格验收）

- [ ] `RunState`/`Message` 序列化往返相等（任务 1）
- [ ] `TrajectorySink` 把 run 完整轨迹按序落库（任务 2/5）
- [ ] loop 注入 checkpoint_store → 每步边界存快照、RunFinished 删（任务 4）
- [ ] `resume(run_id)` 从 step+1 续、产出 RunFinished、恢复历史；不存在的 run_id → RunError（任务 4）
- [ ] **向后兼容**：无 checkpoint_store 的 run 事件序列与现状一致、①原有 loop 测试不回归（任务 4）
- [ ] 无新依赖；`uv run pytest` 全绿（任务 5）
```

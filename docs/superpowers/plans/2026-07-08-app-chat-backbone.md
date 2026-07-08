# AI 学习助手 · App-1（聊天脊柱）实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** FastAPI 后端把 harness 装配成服务 + `/api/chat` SSE 流式 + 会话 CRUD；React SPA 聊天页实时渲染 agent 进度。

**架构：** 后端 `app/`（config/assembly/conversations/context/api），harness 作库导入零改动；前端 `web/`（Vite+React+TS+Tailwind），fetch 流式读 SSE。

**技术栈：** FastAPI + uvicorn（后端）· Vite+React+TS+Tailwind+Vitest（前端）· harness 不加依赖不改代码。

**规格：** `docs/superpowers/specs/2026-07-08-app-chat-backbone-design.md`

**提交规范：** git 身份已是 sumengnan，默认提交；**任何 commit message 不得出现 Claude/AI/Co-Authored-By 等署名**。

---

## 文件结构与职责

| 文件 | 动作 | 职责 |
|---|---|---|
| `app/config.py` | 新增 | `AppConfig`（继承 HarnessConfig + app 字段） |
| `app/context.py` | 新增 | `ConversationContextManager` |
| `app/conversations.py` | 新增 | `ConversationStore`（SQLite） |
| `app/assembly.py` | 新增 | `build_harness(config) → Harness` |
| `app/api/chat.py` | 新增 | `make_chat_router`（SSE） |
| `app/api/conversations.py` | 新增 | `make_conversations_router`（CRUD） |
| `app/main.py` | 新增 | `create_app(config, harness, store)` |
| `web/*` | 新增 | React SPA（脚手架 + 组件） |
| `pyproject.toml` | 改 | fastapi/uvicorn 依赖 + pythonpath 加 `.` |

**注意**：后端测试放 `tests/app/`，复用 `tests/conftest.py` 的 `make_mock`/`text_turn` fixture（pytest conftest 对子目录生效）。

---

## 任务 0：依赖与 AppConfig

**文件：** 改 `pyproject.toml`、创建 `app/__init__.py`、`app/config.py`、测试 `tests/app/__init__.py`(空)、`tests/app/test_config.py`

- [ ] **步骤 1：`pyproject.toml`** —— `dependencies` 追加：
```toml
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
```
并把 `[tool.pytest.ini_options]` 的 `pythonpath` 改为 `["src", "."]`（让 `app` 可导入）。

- [ ] **步骤 2：`uv sync`**　运行：`uv sync`　预期：装上 fastapi/uvicorn。

- [ ] **步骤 3：建包 + 写失败测试**

运行：`mkdir -p app/api tests/app && touch app/__init__.py app/api/__init__.py tests/app/__init__.py`

```python
# tests/app/test_config.py
from app.config import AppConfig


def test_app_defaults():
    cfg = AppConfig(api_key="k")
    assert cfg.app_host == "127.0.0.1"
    assert cfg.app_port == 8000
    assert cfg.conversations_db_path == "conversations.db"
    assert cfg.enable_sandbox is False
    assert cfg.model == "gpt-4o-mini"      # 继承 HarnessConfig
```

运行：`uv run pytest tests/app/test_config.py -v`　预期 FAIL。

- [ ] **步骤 4：实现 `app/config.py`**

```python
# app/config.py
from __future__ import annotations

from pydantic_settings import SettingsConfigDict

from harness.config import HarnessConfig


class AppConfig(HarnessConfig):
    model_config = SettingsConfigDict(
        env_prefix="HARNESS_", env_file=".env", extra="ignore", protected_namespaces=())

    app_host: str = "127.0.0.1"
    app_port: int = 8000
    conversations_db_path: str = "conversations.db"
    app_system_prompt: str = "你是一个 AI 学习助手，可用工具检索知识、联网、计算来帮助用户学习。"
    enable_browser: bool = False
    enable_sandbox: bool = False
    enable_dispatch: bool = False
    cors_origins: list = ["http://localhost:5173"]
```

- [ ] **步骤 5：跑通并 commit**

运行：`uv run pytest tests/app/test_config.py -v`　预期全 pass。
```bash
git add pyproject.toml uv.lock app/__init__.py app/api/__init__.py app/config.py tests/app/__init__.py tests/app/test_config.py
git commit -m "chore: app 依赖与 AppConfig"
```

---

## 任务 1：ConversationContextManager

**文件：** 创建 `app/context.py`、测试 `tests/app/test_context.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/app/test_context.py
from app.context import ConversationContextManager
from harness.state import RunState
from harness.types import Message, Role


def test_build_injects_history_in_order():
    hist = [Message(role=Role.USER, content="旧问"), Message(role=Role.ASSISTANT, content="旧答")]
    ctx = ConversationContextManager("你是助手", hist)
    st = RunState(run_id="r1"); st.append(Message(role=Role.USER, content="新问"))
    built = ctx.build(st)
    assert built[0].role == Role.SYSTEM
    assert [m.content for m in built] == ["你是助手", "旧问", "旧答", "新问"]
```

运行：预期 FAIL。

- [ ] **步骤 2：实现 `app/context.py`**

```python
# app/context.py
from __future__ import annotations

from harness.state import RunState
from harness.types import Message, Role


class ConversationContextManager:
    """注入对话历史的 ContextManager（鸭子类型 harness.ContextManager）。"""

    def __init__(self, system_prompt: str, history: list[Message]) -> None:
        self._system = system_prompt
        self._history = history

    def build(self, state: RunState) -> list[Message]:
        return [Message(role=Role.SYSTEM, content=self._system), *self._history, *state.messages]
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/app/test_context.py -v`　预期：1 passed。
```bash
git add app/context.py tests/app/test_context.py
git commit -m "feat: ConversationContextManager 注入对话历史"
```

---

## 任务 2：ConversationStore

**文件：** 创建 `app/conversations.py`、测试 `tests/app/test_conversations.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/app/test_conversations.py
from app.conversations import ConversationStore
from harness.types import Message, Role


def test_create_list_delete():
    s = ConversationStore(":memory:")
    cid = s.create("测试")
    assert any(c["id"] == cid and c["title"] == "测试" for c in s.list())
    assert s.exists(cid) is True
    s.delete(cid)
    assert s.exists(cid) is False


def test_append_and_messages_roundtrip():
    s = ConversationStore(":memory:")
    cid = s.create()
    s.append(cid, [Message(role=Role.USER, content="hi"),
                   Message(role=Role.ASSISTANT, content="yo")])
    s.append(cid, [Message(role=Role.USER, content="再问")])
    msgs = s.messages(cid)
    assert [m.content for m in msgs] == ["hi", "yo", "再问"]
    assert msgs[0].role == Role.USER and msgs[1].role == Role.ASSISTANT
```

运行：预期 FAIL。

- [ ] **步骤 2：实现 `app/conversations.py`**

```python
# app/conversations.py
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from harness.persistence.serialize import message_from_dict, message_to_dict
from harness.types import Message


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationStore:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS conversations("
            "id TEXT PRIMARY KEY, title TEXT, created_at TEXT)")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS conversation_messages("
            "conv_id TEXT, seq INTEGER, role TEXT, content TEXT, tool_calls TEXT, "
            "tool_call_id TEXT, created_at TEXT, PRIMARY KEY(conv_id, seq))")
        self._conn.commit()

    def create(self, title: str = "新对话") -> str:
        cid = uuid.uuid4().hex
        self._conn.execute("INSERT INTO conversations(id, title, created_at) VALUES (?, ?, ?)",
                           (cid, title, _now()))
        self._conn.commit()
        return cid

    def list(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, title, created_at FROM conversations ORDER BY created_at DESC").fetchall()
        return [{"id": r[0], "title": r[1], "created_at": r[2]} for r in rows]

    def exists(self, conv_id: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM conversations WHERE id = ?", (conv_id,)).fetchone() is not None

    def messages(self, conv_id: str) -> list[Message]:
        rows = self._conn.execute(
            "SELECT role, content, tool_calls, tool_call_id FROM conversation_messages "
            "WHERE conv_id = ? ORDER BY seq", (conv_id,)).fetchall()
        out: list[Message] = []
        for role, content, tool_calls, tool_call_id in rows:
            out.append(message_from_dict({
                "role": role, "content": content,
                "tool_calls": json.loads(tool_calls) if tool_calls else [],
                "tool_call_id": tool_call_id,
            }))
        return out

    def append(self, conv_id: str, msgs: list[Message]) -> None:
        seq = self._conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 FROM conversation_messages WHERE conv_id = ?",
            (conv_id,)).fetchone()[0]
        for m in msgs:
            d = message_to_dict(m)
            self._conn.execute(
                "INSERT INTO conversation_messages(conv_id, seq, role, content, tool_calls, "
                "tool_call_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (conv_id, seq, d["role"], d["content"],
                 json.dumps(d["tool_calls"], ensure_ascii=False) if d["tool_calls"] else None,
                 d["tool_call_id"], _now()))
            seq += 1
        self._conn.commit()

    def delete(self, conv_id: str) -> None:
        self._conn.execute("DELETE FROM conversation_messages WHERE conv_id = ?", (conv_id,))
        self._conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        self._conn.commit()
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/app/test_conversations.py -v`　预期：2 passed。
```bash
git add app/conversations.py tests/app/test_conversations.py
git commit -m "feat: ConversationStore 多轮对话存储"
```

---

## 任务 3：装配 build_harness（config 门控）

**文件：** 创建 `app/assembly.py`、测试 `tests/app/test_assembly.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/app/test_assembly.py
from app.config import AppConfig
from app.assembly import build_harness, Harness


def _cfg(**kw):
    return AppConfig(api_key="k", persistence_db_path=":memory:", memory_db_path=":memory:", **kw)


def test_core_tools_registered_heavy_gated_off():
    h = build_harness(_cfg(enable_browser=False, enable_sandbox=False))
    assert isinstance(h, Harness)
    assert h.registry.get("calculator") is not None
    assert h.registry.get("http_request") is not None
    assert h.registry.get("search_memory") is not None    # api_key 有 → 记忆注册
    assert h.registry.get("browse") is None                # 未启用
    assert h.registry.get("run_python") is None             # 未启用沙箱


def test_browser_gated_on():
    h = build_harness(_cfg(enable_browser=True))
    assert h.registry.get("browse") is not None
```

运行：预期 FAIL。

- [ ] **步骤 2：实现 `app/assembly.py`**

```python
# app/assembly.py
from __future__ import annotations

from dataclasses import dataclass

from harness.llm.openai_compat import OpenAICompatibleClient
from harness.persistence.checkpoint import CheckpointStore
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink
from harness.reliability.retry import RetryingModelClient
from harness.tools.base import ToolRegistry
from harness.tools.builtins.calculator import CalculatorTool
from harness.tools.builtins.http_tool import HttpRequestTool


@dataclass
class Harness:
    client: object
    registry: ToolRegistry
    checkpoint_store: CheckpointStore
    trajectory_store: TrajectoryStore
    sink: TrajectorySink
    system_prompt: str


def build_harness(config) -> Harness:
    client = RetryingModelClient(
        OpenAICompatibleClient(config),
        max_retries=config.max_retries, base_delay=config.retry_base_delay)

    reg = ToolRegistry()
    reg.register(CalculatorTool())
    reg.register(HttpRequestTool(
        config.http_allowed_domains, config.http_block_private, config.http_timeout,
        config.http_max_response_bytes, config.http_max_redirects))

    # 记忆（有 api_key 即可注册；知识库为空时检索返回空，不报错）
    if config.api_key or config.embedding_api_key:
        from harness.memory.embeddings import OpenAICompatibleEmbeddingClient
        from harness.memory.store import MemoryStore
        from harness.memory.memory import Memory
        from harness.memory.episodic import EpisodicMemory
        from harness.tools.builtins.memory_search import SearchMemoryTool
        from harness.tools.builtins.memory_write import RememberTool
        from harness.tools.builtins.episode_tools import RecallEpisodesTool
        embedder = OpenAICompatibleEmbeddingClient(
            config.embedding_base_url, config.embedding_api_key or config.api_key,
            config.embedding_model, config.embedding_dimension)
        mem = Memory(MemoryStore(config.memory_db_path, config.embedding_dimension), embedder,
                     config.chunk_size, config.chunk_overlap)
        reg.register(SearchMemoryTool(mem, default_k=config.search_top_k))
        reg.register(RememberTool(mem))
        reg.register(RecallEpisodesTool(EpisodicMemory(mem), default_k=config.episode_recall_k))

    if config.enable_browser:
        from harness.browser.factory import build_browser
        from harness.tools.builtins.browse_tool import BrowseTool
        reg.register(BrowseTool(
            build_browser(config), config.http_allowed_domains, config.http_block_private,
            config.browser_nav_timeout, config.browser_wait_until, config.browser_output_max_chars))

    if config.enable_sandbox and config.sandbox_docker_host:
        from harness.sandbox.factory import build_sandbox
        from harness.tools.builtins.fs_tools import WriteFileTool, ReadFileTool, ListFilesTool
        from harness.tools.builtins.shell_tool import RunShellTool
        from harness.tools.builtins.code_tool import RunPythonTool
        sb = build_sandbox(config)
        reg.register(WriteFileTool(sb))
        reg.register(ReadFileTool(sb, config.sandbox_output_max_chars))
        reg.register(ListFilesTool(sb))
        reg.register(RunShellTool(sb, config.sandbox_exec_timeout, config.sandbox_output_max_chars))
        reg.register(RunPythonTool(sb, config.sandbox_exec_timeout, config.sandbox_output_max_chars))

    traj = TrajectoryStore(config.persistence_db_path)
    return Harness(
        client=client, registry=reg,
        checkpoint_store=CheckpointStore(config.persistence_db_path),
        trajectory_store=traj, sink=TrajectorySink(traj),
        system_prompt=config.app_system_prompt)
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/app/test_assembly.py -v`　预期：2 passed。
```bash
git add app/assembly.py tests/app/test_assembly.py
git commit -m "feat: build_harness 装配（config 门控工具）"
```

---

## 任务 4：会话 CRUD API

**文件：** 创建 `app/api/conversations.py`、测试并入任务 5 的 `tests/app/test_api.py`（先只测 CRUD）

- [ ] **步骤 1：写失败测试**（`tests/app/test_api.py`，本任务先加 CRUD 用例）

```python
# tests/app/test_api.py
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import AppConfig
from app.assembly import Harness
from app.conversations import ConversationStore
from harness.tools.base import ToolRegistry
from harness.tools.builtins.calculator import CalculatorTool
from harness.persistence.checkpoint import CheckpointStore
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink


def _fake_harness(make_mock, turns):
    reg = ToolRegistry(); reg.register(CalculatorTool())
    traj = TrajectoryStore(":memory:")
    return Harness(client=make_mock(turns), registry=reg,
                   checkpoint_store=CheckpointStore(":memory:"),
                   trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="你是助手")


def _client(make_mock, turns=None):
    store = ConversationStore(":memory:")
    app = create_app(config=AppConfig(api_key="k"),
                     harness=_fake_harness(make_mock, turns or []), store=store)
    return TestClient(app), store


def test_conversation_crud(make_mock):
    client, _ = _client(make_mock)
    cid = client.post("/api/conversations", json={"title": "T"}).json()["id"]
    assert any(c["id"] == cid for c in client.get("/api/conversations").json())
    assert client.get(f"/api/conversations/{cid}/messages").json() == []
    client.delete(f"/api/conversations/{cid}")
    assert client.get(f"/api/conversations/{cid}/messages").status_code == 404
```

运行：预期 FAIL（模块缺失）。

- [ ] **步骤 2：实现 `app/api/conversations.py`**

```python
# app/api/conversations.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class _Create(BaseModel):
    title: str | None = None


def make_conversations_router(store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/conversations")
    async def list_conversations():
        return store.list()

    @router.post("/api/conversations")
    async def create_conversation(body: _Create):
        return {"id": store.create(body.title or "新对话")}

    @router.get("/api/conversations/{conv_id}/messages")
    async def get_messages(conv_id: str):
        if not store.exists(conv_id):
            raise HTTPException(status_code=404, detail="对话不存在")
        return [{"role": m.role.value, "content": m.content} for m in store.messages(conv_id)]

    @router.delete("/api/conversations/{conv_id}")
    async def delete_conversation(conv_id: str):
        store.delete(conv_id)
        return {"ok": True}

    return router
```

（`app/main.py::create_app` 在任务 5 一起实现，本任务测试依赖它——若想任务 4 独立跑通，可先写一个最小 `create_app` 只挂 conversations router，任务 5 再补 chat。为简洁，**建议任务 4、5 连续实现**：本步骤先让 `test_conversation_crud` 在任务 5 的 `create_app` 完成后一起转绿。）

- [ ] **步骤 3：commit（与任务 5 一起转绿后）**

```bash
git add app/api/conversations.py tests/app/test_api.py
git commit -m "feat: 会话 CRUD API"
```

---

## 任务 5：chat SSE API + create_app

**文件：** 创建 `app/api/chat.py`、`app/main.py`、补 `tests/app/test_api.py` 的 SSE + 多轮用例

- [ ] **步骤 1：补失败测试**（`tests/app/test_api.py` 追加）

```python
import json


def _sse_events(resp):
    events = []
    for line in resp.iter_lines():
        if line and line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def test_chat_streams_sse_and_persists(make_mock, text_turn):
    client, store = _client(make_mock, [text_turn("你好呀")])
    cid = client.post("/api/conversations", json={}).json()["id"]
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": "hi"}) as resp:
        assert resp.status_code == 200
        types = [e["type"] for e in _sse_events(resp)]
    assert "TextDelta" in types and "RunFinished" in types
    msgs = [m.content for m in store.messages(cid)]
    assert msgs == ["hi", "你好呀"]                     # 用户问 + 最终答落库


def test_chat_tool_call_in_stream(make_mock, text_turn, tool_turn):
    turns = [tool_turn("calculator", '{"expression":"(12+8)*3"}', call_id="c1"),
             text_turn("答案是 60")]
    client, store = _client(make_mock, turns)
    cid = client.post("/api/conversations", json={}).json()["id"]
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": "算 (12+8)*3"}) as resp:
        events = _sse_events(resp)
    tfs = [e for e in events if e["type"] == "ToolFinished"]
    assert tfs and tfs[0]["data"]["result"]["content"] == "60"


def test_two_turns_accumulate_history(make_mock, text_turn):
    client, store = _client(make_mock, [text_turn("答1"), text_turn("答2")])
    cid = client.post("/api/conversations", json={}).json()["id"]
    for msg in ["问1", "问2"]:
        with client.stream("POST", "/api/chat",
                           json={"conversation_id": cid, "message": msg}) as resp:
            _sse_events(resp)
    assert [m.content for m in store.messages(cid)] == ["问1", "答1", "问2", "答2"]


def test_chat_unknown_conversation_404(make_mock, text_turn):
    client, _ = _client(make_mock, [text_turn("x")])
    resp = client.post("/api/chat", json={"conversation_id": "nope", "message": "hi"})
    assert resp.status_code == 404
```

运行：预期 FAIL。

- [ ] **步骤 2：实现 `app/api/chat.py`**

```python
# app/api/chat.py
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from harness.events import RunError, RunFinished
from harness.loop.agent_loop import AgentLoop
from harness.persistence.serialize import event_to_dict
from harness.reliability.budget import BudgetTracker
from harness.types import Message, Role

from ..context import ConversationContextManager


class _ChatRequest(BaseModel):
    conversation_id: str
    message: str


def make_chat_router(harness, store, config) -> APIRouter:
    router = APIRouter()

    @router.post("/api/chat")
    async def chat(req: _ChatRequest):
        if not store.exists(req.conversation_id):
            raise HTTPException(status_code=404, detail="对话不存在")
        history = store.messages(req.conversation_id)
        ctx = ConversationContextManager(harness.system_prompt, history)
        loop = AgentLoop(
            client=harness.client, registry=harness.registry, context=ctx,
            max_steps=config.max_steps,
            budget=BudgetTracker(config.max_tokens_budget, config.max_wall_seconds),
            checkpoint_store=harness.checkpoint_store, model_name=config.model,
            price_map=config.price_map, tool_result_max_chars=config.tool_result_max_chars)

        async def gen():
            final = None
            async for ev in harness.sink.wrap(loop.run(req.message)):
                if isinstance(ev, RunFinished):
                    final = ev.message.content
                elif isinstance(ev, RunError):
                    final = final or f"[出错] {ev.error}"
                yield f"data: {json.dumps(event_to_dict(ev), ensure_ascii=False)}\n\n"
            store.append(req.conversation_id, [
                Message(role=Role.USER, content=req.message),
                Message(role=Role.ASSISTANT, content=final or ""),
            ])

        return StreamingResponse(gen(), media_type="text/event-stream")

    return router
```

- [ ] **步骤 3：实现 `app/main.py`**

```python
# app/main.py
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.chat import make_chat_router
from .api.conversations import make_conversations_router
from .assembly import build_harness
from .config import AppConfig
from .conversations import ConversationStore


def create_app(config: AppConfig | None = None, harness=None, store=None) -> FastAPI:
    config = config or AppConfig()
    harness = harness if harness is not None else build_harness(config)
    store = store if store is not None else ConversationStore(config.conversations_db_path)

    app = FastAPI(title="AI 学习助手")
    app.add_middleware(
        CORSMiddleware, allow_origins=config.cors_origins,
        allow_methods=["*"], allow_headers=["*"])
    app.include_router(make_conversations_router(store))
    app.include_router(make_chat_router(harness, store, config))

    if os.path.isdir("web/dist"):  # prod：托管前端静态产物
        app.mount("/", StaticFiles(directory="web/dist", html=True), name="static")
    return app
```

> **注意**：**不要**在模块级写 `app = create_app()`——那会在 import 时就构造真实 harness（空 api_key 调 `AsyncOpenAI` 会抛错，导致测试 import 失败）。用 uvicorn `--factory` 模式启动：`uvicorn --factory app.main:create_app`。测试里直接 `from app.main import create_app` 并注入 fake harness，不触发真实装配。

- [ ] **步骤 4：跑通并 commit**

运行：`uv run pytest tests/app/ -v`　预期：全 pass（config/context/conversations/assembly/api 全绿）。再 `uv run pytest -q` 确认 harness 原有测试无回归。
```bash
git add app/api/chat.py app/main.py tests/app/test_api.py
git commit -m "feat: chat SSE API + create_app 装配"
```

---

## 任务 6：React 前端脚手架

**文件：** 创建 `web/`（package.json、vite/tailwind/ts 配置、index.html、main.tsx、index.css、types.ts）

- [ ] **步骤 1：`web/package.json`**

```json
{
  "name": "ai-learning-helper-web",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "test": "vitest run"
  },
  "dependencies": { "react": "^18.3.1", "react-dom": "^18.3.1" },
  "devDependencies": {
    "@types/react": "^18.3.3", "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1", "typescript": "^5.5.3", "vite": "^5.4.0",
    "tailwindcss": "^3.4.7", "postcss": "^8.4.40", "autoprefixer": "^10.4.19",
    "vitest": "^2.0.5", "@testing-library/react": "^16.0.0", "jsdom": "^24.1.1"
  }
}
```

- [ ] **步骤 2：配置文件**

```ts
// web/vite.config.ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  test: { environment: "jsdom" },
});
```
```js
// web/tailwind.config.js
export default { content: ["./index.html", "./src/**/*.{ts,tsx}"], theme: { extend: {} }, plugins: [] };
```
```js
// web/postcss.config.js
export default { plugins: { tailwindcss: {}, autoprefixer: {} } };
```
```json
// web/tsconfig.json
{ "compilerOptions": { "target": "ES2020", "lib": ["ES2020","DOM","DOM.Iterable"],
  "module": "ESNext", "moduleResolution": "Bundler", "jsx": "react-jsx",
  "strict": true, "skipLibCheck": true, "types": ["vitest/globals"] },
  "include": ["src"] }
```
```html
<!-- web/index.html -->
<!doctype html><html lang="zh"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>AI 学习助手</title></head>
<body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body></html>
```

- [ ] **步骤 3：`web/src/index.css`、`main.tsx`、`types.ts`**

```css
/* web/src/index.css */
@tailwind base; @tailwind components; @tailwind utilities;
html, body, #root { height: 100%; margin: 0; }
```
```tsx
// web/src/main.tsx
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><App /></React.StrictMode>);
```
```ts
// web/src/types.ts
export type AgentEvent = { type: string; data: any };
export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  steps?: { tool: string; args: any; result?: string; isError?: boolean }[];
  usage?: { tokens: number; cost: number | null };
};
export type Conversation = { id: string; title: string; created_at: string };
```

- [ ] **步骤 4：commit**（脚手架，先不跑测试）
```bash
git add web/package.json web/vite.config.ts web/tailwind.config.js web/postcss.config.js web/tsconfig.json web/index.html web/src/index.css web/src/main.tsx web/src/types.ts
git commit -m "chore: React 前端脚手架（Vite+TS+Tailwind）"
```

---

## 任务 7：前端 API 客户端 + 组件 + Vitest

**文件：** 创建 `web/src/api/client.ts`、`App.tsx`、`components/{ConversationList,ChatView,AgentProgress}.tsx`、`web/src/api/sse.test.ts`

- [ ] **步骤 1：`web/src/api/client.ts`**（SSE 解析抽成纯函数 `drainSSE` 供测试）

```ts
// web/src/api/client.ts
import type { AgentEvent, Conversation } from "../types";

export function drainSSE(buffer: string): { events: AgentEvent[]; rest: string } {
  const events: AgentEvent[] = [];
  let idx: number;
  while ((idx = buffer.indexOf("\n\n")) >= 0) {
    const chunk = buffer.slice(0, idx);
    buffer = buffer.slice(idx + 2);
    const line = chunk.split("\n").find((l) => l.startsWith("data: "));
    if (line) events.push(JSON.parse(line.slice(6)) as AgentEvent);
  }
  return { events, rest: buffer };
}

export async function streamChat(
  conversationId: string, message: string, onEvent: (e: AgentEvent) => void,
): Promise<void> {
  const resp = await fetch("/api/chat", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conversation_id: conversationId, message }),
  });
  if (!resp.ok || !resp.body) throw new Error(`chat 失败：${resp.status}`);
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = drainSSE(buffer);
    buffer = rest;
    events.forEach(onEvent);
  }
}

export const api = {
  list: (): Promise<Conversation[]> => fetch("/api/conversations").then((r) => r.json()),
  create: (title?: string): Promise<{ id: string }> =>
    fetch("/api/conversations", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }) }).then((r) => r.json()),
  messages: (id: string): Promise<{ role: string; content: string }[]> =>
    fetch(`/api/conversations/${id}/messages`).then((r) => r.json()),
  remove: (id: string): Promise<void> =>
    fetch(`/api/conversations/${id}`, { method: "DELETE" }).then(() => undefined),
};
```

- [ ] **步骤 2：Vitest 单测 `web/src/api/sse.test.ts`**

```ts
// web/src/api/sse.test.ts
import { describe, it, expect } from "vitest";
import { drainSSE } from "./client";

describe("drainSSE", () => {
  it("解析完整事件、保留残缺尾巴", () => {
    const input =
      'data: {"type":"TextDelta","data":{"text":"hi"}}\n\n' +
      'data: {"type":"RunFinished","data":{}}\n\n' +
      "data: {\"type\":\"Par";
    const { events, rest } = drainSSE(input);
    expect(events.map((e) => e.type)).toEqual(["TextDelta", "RunFinished"]);
    expect(rest.startsWith("data: ")).toBe(true);   // 残缺片段留待下次
  });
});
```

运行：`cd web && npm install && npm run test`　预期：1 passed（首次需 `npm install`）。

- [ ] **步骤 3：组件 `AgentProgress.tsx` / `ChatView.tsx` / `ConversationList.tsx` / `App.tsx`**

```tsx
// web/src/components/AgentProgress.tsx
import type { ChatMessage } from "../types";
export function AgentProgress({ steps }: { steps: NonNullable<ChatMessage["steps"]> }) {
  if (!steps.length) return null;
  return (
    <div className="mt-2 space-y-1">
      {steps.map((s, i) => (
        <details key={i} className="text-xs bg-gray-100 rounded px-2 py-1">
          <summary className={s.isError ? "text-red-600" : "text-gray-600"}>
            {s.result === undefined ? "调用工具" : "工具完成"}：{s.tool}
          </summary>
          <div className="mt-1 text-gray-500 break-all">参数：{JSON.stringify(s.args)}</div>
          {s.result !== undefined && <div className="mt-1 break-all">结果：{s.result}</div>}
        </details>
      ))}
    </div>
  );
}
```
```tsx
// web/src/components/ChatView.tsx
import { useState } from "react";
import type { ChatMessage } from "../types";
import { streamChat } from "../api/client";
import { AgentProgress } from "./AgentProgress";

export function ChatView({ conversationId, initial }: { conversationId: string; initial: ChatMessage[] }) {
  const [messages, setMessages] = useState<ChatMessage[]>(initial);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  async function send() {
    if (!input.trim() || busy) return;
    const userMsg: ChatMessage = { role: "user", content: input };
    const assistant: ChatMessage = { role: "assistant", content: "", steps: [] };
    setMessages((m) => [...m, userMsg, assistant]);
    const msg = input; setInput(""); setBusy(true);
    const upd = (fn: (a: ChatMessage) => void) =>
      setMessages((m) => { const copy = [...m]; fn(copy[copy.length - 1]); return copy; });
    try {
      await streamChat(conversationId, msg, (e) => {
        if (e.type === "TextDelta") upd((a) => { a.content += e.data.text; });
        else if (e.type === "ToolStarted") upd((a) => a.steps!.push({ tool: e.data.tool_call.name, args: e.data.tool_call.arguments }));
        else if (e.type === "ToolFinished") upd((a) => {
          const s = a.steps![a.steps!.length - 1];
          if (s) { s.result = e.data.result.content; s.isError = e.data.result.is_error; }
        });
        else if (e.type === "ModelUsage") upd((a) => { a.usage = { tokens: e.data.usage.total, cost: e.data.cost_usd }; });
        else if (e.type === "RunError") upd((a) => { a.content += `\n[出错] ${e.data.error}`; });
      });
    } finally { setBusy(false); }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "text-right" : "text-left"}>
            <div className={`inline-block max-w-[80%] rounded-lg px-3 py-2 whitespace-pre-wrap ${
              m.role === "user" ? "bg-blue-500 text-white" : "bg-gray-200"}`}>
              {m.content || (m.role === "assistant" ? "…" : "")}
              {m.role === "assistant" && m.steps && <AgentProgress steps={m.steps} />}
              {m.usage && <div className="mt-1 text-xs text-gray-500">tokens {m.usage.tokens}{m.usage.cost != null ? ` · $${m.usage.cost.toFixed(4)}` : ""}</div>}
            </div>
          </div>
        ))}
      </div>
      <div className="p-3 border-t flex gap-2">
        <input className="flex-1 border rounded px-3 py-2" value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()} placeholder="问点什么…" />
        <button className="bg-blue-500 text-white rounded px-4 disabled:opacity-50"
          onClick={send} disabled={busy}>发送</button>
      </div>
    </div>
  );
}
```
```tsx
// web/src/components/ConversationList.tsx
import type { Conversation } from "../types";
export function ConversationList({ items, activeId, onSelect, onNew, onDelete }: {
  items: Conversation[]; activeId: string | null;
  onSelect: (id: string) => void; onNew: () => void; onDelete: (id: string) => void;
}) {
  return (
    <div className="w-60 border-r flex flex-col">
      <button className="m-2 bg-blue-500 text-white rounded py-2" onClick={onNew}>+ 新对话</button>
      <div className="flex-1 overflow-y-auto">
        {items.map((c) => (
          <div key={c.id}
            className={`px-3 py-2 cursor-pointer flex justify-between group ${c.id === activeId ? "bg-gray-200" : "hover:bg-gray-100"}`}
            onClick={() => onSelect(c.id)}>
            <span className="truncate">{c.title}</span>
            <button className="opacity-0 group-hover:opacity-100 text-red-500"
              onClick={(e) => { e.stopPropagation(); onDelete(c.id); }}>×</button>
          </div>
        ))}
      </div>
    </div>
  );
}
```
```tsx
// web/src/App.tsx
import { useEffect, useState } from "react";
import type { Conversation, ChatMessage } from "./types";
import { api } from "./api/client";
import { ConversationList } from "./components/ConversationList";
import { ChatView } from "./components/ChatView";

export default function App() {
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [initial, setInitial] = useState<ChatMessage[]>([]);

  const refresh = () => api.list().then(setConvs);
  useEffect(() => { refresh(); }, []);

  async function select(id: string) {
    setActiveId(id);
    const msgs = await api.messages(id);
    setInitial(msgs.map((m) => ({ role: m.role as "user" | "assistant", content: m.content })));
  }
  async function newConv() { const { id } = await api.create(); await refresh(); await select(id); }
  async function del(id: string) { await api.remove(id); await refresh(); if (id === activeId) { setActiveId(null); setInitial([]); } }

  return (
    <div className="flex h-full">
      <ConversationList items={convs} activeId={activeId} onSelect={select} onNew={newConv} onDelete={del} />
      <div className="flex-1">
        {activeId
          ? <ChatView key={activeId} conversationId={activeId} initial={initial} />
          : <div className="h-full flex items-center justify-center text-gray-400">新建或选择一个对话开始</div>}
      </div>
    </div>
  );
}
```

- [ ] **步骤 4：跑前端测试并 commit**

运行：`cd web && npm run test`　预期：1 passed（drainSSE）。
```bash
git add web/src/api/client.ts web/src/api/sse.test.ts web/src/App.tsx web/src/components/*.tsx
git commit -m "feat: 前端 SSE 客户端 + 聊天/会话组件"
```

---

## 任务 8：联调与手动验收 + 运行说明

**文件：** 新增 `app/README.md`（运行说明）

- [ ] **步骤 1：全量后端测试**

运行：`uv run pytest -q`　预期：全绿（harness 原有 + app 新增；既有 3 个 skip 不变）。

- [ ] **步骤 2：手动 E2E（需真实聊天端点 `.env`）**

```bash
# 后端（--factory：延迟构造，避免 import 期装配）
uv run uvicorn --factory app.main:create_app --reload
# 前端（另一终端）
cd web && npm install && npm run dev
# 浏览器打开 Vite 给出的 URL（默认 http://localhost:5173）
```
验收：新建对话 → 发 "帮我算 (12+8)*3" → 实时看到打字 + "调用工具 calculator" + 结果 60 + 最终答案；再发一条 → 能延续上下文；侧栏切换/删除对话正常。

- [ ] **步骤 3：写 `app/README.md`**（记录上面的 dev/prod 运行方式、`.env` 需要的 key、`enable_*` 开关）。

- [ ] **步骤 4：commit**
```bash
git add app/README.md
git commit -m "docs: App-1 运行说明"
```

---

## 完成标准（对照规格验收）

- [ ] `/api/chat` SSE 流式：`TextDelta`+`RunFinished`，含工具轮有 `ToolFinished`（任务 5）
- [ ] 真工具调用（calculator）结果回填（任务 5）
- [ ] 多轮：对话历史累积、context 注入（任务 1 + 任务 5 accumulate 测试）
- [ ] 会话 CRUD 全通（任务 4/5）
- [ ] 配置门控：browser/sandbox 未启用则不注册（任务 3）
- [ ] 前端 SSE 解析单测通过、组件齐全、手动 E2E 通（任务 6/7/8）
- [ ] harness 原有测试无回归、harness 零改动（`uv run pytest` 全绿）
```

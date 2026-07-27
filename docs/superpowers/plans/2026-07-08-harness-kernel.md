> ⚠️ **历史设计记录（已过时）**：harness 内核已抽成外部包
> [ai-harness-framework](https://github.com/sumengnan/ai-harness-framework)（import 名仍是 `harness`）。
> 本文是带日期的设计存档，文中的 `src/harness/` 路径与打包配置反映**当时**的仓库结构、未随抽包更新；
> 当前结构以 [架构文档](../../architecture-harness.md) 为准。

# AI Harness 内核骨架 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 用纯 Python + 全异步，构建一个最小可跑的 Agent 运行时——对话闭环 + 工具调用 + 结果回填 + 事件流对外，自带 `calculator` 玩具工具。

**架构：** 四个职责单一的单元（`llm` 模型抽象 / `tools` 工具系统 / `context` 上下文 / `loop` 编排内核），依赖单向 `loop → {llm, tools, context}`，共享 `types`/`events`/`state`。`AgentLoop.run()` 是 async generator，逐条 yield 结构化事件。用 `MockModelClient` 做不打真实 API 的确定性测试。（注：这是本计划落地的**最初内核**；后续 `reliability`/`telemetry`/`approval` 等 spec 又为 `loop` 引入了横切依赖，方向仍单向向下、无环，现状见下方「依赖方向」。）

**技术栈：** Python 3.11+ · `uv` · `openai`(async) · `pydantic` v2 · `pydantic-settings` · `pytest` + `pytest-asyncio`。

**规格：** `docs/superpowers/specs/2026-07-08-harness-kernel-design.md`

---

## 文件结构与职责

| 文件 | 职责 |
|---|---|
| `src/harness/types.py` | 核心数据类型：`Role` / `ToolCall` / `ToolResult` / `Message`（含 `to_openai()`） |
| `src/harness/events.py` | 事件类型：`RunStarted` / `StepStarted` / `TextDelta` / `ToolCallRequested` / `ToolStarted` / `ToolFinished` / `StepFinished` / `RunFinished` / `RunError` |
| `src/harness/state.py` | `RunState`：会话状态（run_id + 消息历史 + 步数） |
| `src/harness/config.py` | `HarnessConfig`：pydantic-settings 配置 |
| `src/harness/llm/base.py` | `ModelClient` 协议 + `StreamChunk` + `ToolCallDelta` |
| `src/harness/llm/openai_compat.py` | `OpenAICompatibleClient`：真实端点实现 |
| `src/harness/tools/base.py` | `Tool` 基类 + `ToolRegistry` + `ToolExecutor` |
| `src/harness/tools/builtins/calculator.py` | `CalculatorTool` + 受限 AST 求值 |
| `src/harness/context/manager.py` | `ContextManager.build()` |
| `src/harness/loop/agent_loop.py` | `AgentLoop.run()` 编排循环 |
| `tests/conftest.py` | `MockModelClient` + 构造 chunk 的辅助函数 |
| `tests/test_*.py` | 各单元测试 |
| `examples/demo.py` | 订阅事件流逐条打印 |

依赖方向（最初内核）：`loop` 依赖 `llm`/`tools`/`context`；三者互不依赖，只共享 `types`/`events`/`state`。

> **现状更新：** 后续 spec 已为 `loop` 引入横切依赖——`reliability`（预算/重试）、`telemetry`（tracing）、`approval`（工具审批），当前实际为 `loop → {llm, tools, context, reliability, telemetry, approval}`，另共享 `types`/`events`/`state`/`usage`。这些新增依赖方向仍全部单向向下、无环，未破坏分层不变量（`src/harness` 亦不反向依赖 `app`）。

---

## 任务 0：项目脚手架

**文件：**
- 创建：`pyproject.toml`、`.env.example`、`src/harness/__init__.py`、各子包 `__init__.py`

- [ ] **步骤 1：创建 `pyproject.toml`**

```toml
[project]
name = "harness"
version = "0.1.0"
description = "最小 Agent 运行时内核"
requires-python = ">=3.11"
dependencies = [
    "openai>=1.40",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
pythonpath = ["src"]
testpaths = ["tests"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/harness"]
```

- [ ] **步骤 2：创建 `.env.example`**

```bash
HARNESS_API_KEY=sk-your-key
HARNESS_BASE_URL=https://api.openai.com/v1
HARNESS_MODEL=gpt-4o-mini
HARNESS_MAX_STEPS=10
HARNESS_TEMPERATURE=0.7
```

- [ ] **步骤 3：创建包目录与空 `__init__.py`**

运行：
```bash
mkdir -p src/harness/llm src/harness/tools/builtins src/harness/context src/harness/loop tests examples
touch src/harness/__init__.py src/harness/llm/__init__.py src/harness/tools/__init__.py src/harness/tools/builtins/__init__.py src/harness/context/__init__.py src/harness/loop/__init__.py
```

- [ ] **步骤 4：安装依赖并验证**

运行：`uv sync`
预期：成功创建 `.venv` 并安装 openai / pydantic / pytest。

- [ ] **步骤 5：Commit**

```bash
git add pyproject.toml .env.example src tests examples
git commit -m "chore: 项目脚手架与依赖"
```

---

## 任务 1：核心数据类型 `types.py`

**文件：**
- 创建：`src/harness/types.py`
- 测试：`tests/test_types.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_types.py
import json
from harness.types import Role, ToolCall, ToolResult, Message


def test_user_message_to_openai():
    msg = Message(role=Role.USER, content="hi")
    assert msg.to_openai() == {"role": "user", "content": "hi"}


def test_assistant_with_tool_calls_to_openai():
    msg = Message(
        role=Role.ASSISTANT,
        content=None,
        tool_calls=[ToolCall(id="c1", name="calculator", arguments={"expression": "1+1"})],
    )
    out = msg.to_openai()
    assert out["role"] == "assistant"
    assert out["tool_calls"][0]["id"] == "c1"
    assert out["tool_calls"][0]["type"] == "function"
    assert out["tool_calls"][0]["function"]["name"] == "calculator"
    assert json.loads(out["tool_calls"][0]["function"]["arguments"]) == {"expression": "1+1"}


def test_tool_result_message_to_openai():
    msg = Message(role=Role.TOOL, content="2", tool_call_id="c1")
    assert msg.to_openai() == {"role": "tool", "tool_call_id": "c1", "content": "2"}
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_types.py -v`
预期：FAIL，`ModuleNotFoundError: No module named 'harness.types'`

- [ ] **步骤 3：编写实现**

```python
# src/harness/types.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None

    def to_openai(self) -> dict:
        if self.role == Role.TOOL:
            return {
                "role": "tool",
                "tool_call_id": self.tool_call_id,
                "content": self.content or "",
            }
        msg: dict = {"role": self.role.value}
        if self.content is not None:
            msg["content"] = self.content
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in self.tool_calls
            ]
        return msg
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_types.py -v`
预期：3 passed

- [ ] **步骤 5：Commit**

```bash
git add src/harness/types.py tests/test_types.py
git commit -m "feat: 核心数据类型 Message/ToolCall/ToolResult"
```

---

## 任务 2：事件类型 `events.py`

**文件：**
- 创建：`src/harness/events.py`
- 测试：`tests/test_events.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_events.py
from harness.events import (
    RunStarted, StepStarted, TextDelta, ToolCallRequested,
    ToolStarted, ToolFinished, StepFinished, RunFinished, RunError, Event,
)
from harness.types import Message, Role, ToolCall, ToolResult


def test_events_are_event_subclasses():
    assert isinstance(RunStarted(run_id="r1"), Event)
    assert isinstance(TextDelta(text="hi"), Event)


def test_event_payloads():
    assert TextDelta(text="hi").text == "hi"
    assert StepStarted(step=1).step == 1
    tc = ToolCall(id="c1", name="calculator", arguments={})
    assert ToolCallRequested(tool_calls=[tc]).tool_calls == [tc]
    tr = ToolResult(tool_call_id="c1", content="2")
    assert ToolFinished(result=tr).result is tr
    m = Message(role=Role.ASSISTANT, content="done")
    assert RunFinished(message=m).message is m
    assert RunError(error="boom").error == "boom"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_events.py -v`
预期：FAIL，`ModuleNotFoundError: No module named 'harness.events'`

- [ ] **步骤 3：编写实现**

```python
# src/harness/events.py
from __future__ import annotations

from dataclasses import dataclass, field

from .types import Message, ToolCall, ToolResult


class Event:
    """所有事件的基类。"""


@dataclass
class RunStarted(Event):
    run_id: str


@dataclass
class StepStarted(Event):
    step: int


@dataclass
class TextDelta(Event):
    text: str


@dataclass
class ToolCallRequested(Event):
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class ToolStarted(Event):
    tool_call: ToolCall


@dataclass
class ToolFinished(Event):
    result: ToolResult


@dataclass
class StepFinished(Event):
    step: int


@dataclass
class RunFinished(Event):
    message: Message


@dataclass
class RunError(Event):
    error: str
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_events.py -v`
预期：2 passed

- [ ] **步骤 5：Commit**

```bash
git add src/harness/events.py tests/test_events.py
git commit -m "feat: 事件类型定义"
```

---

## 任务 3：会话状态 `state.py`

**文件：**
- 创建：`src/harness/state.py`
- 测试：`tests/test_state.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_state.py
from harness.state import RunState
from harness.types import Message, Role


def test_runstate_append_accumulates():
    state = RunState(run_id="r1")
    assert state.messages == []
    assert state.step == 0
    state.append(Message(role=Role.USER, content="hi"))
    state.append(Message(role=Role.ASSISTANT, content="yo"))
    assert [m.content for m in state.messages] == ["hi", "yo"]
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_state.py -v`
预期：FAIL，`ModuleNotFoundError: No module named 'harness.state'`

- [ ] **步骤 3：编写实现**

```python
# src/harness/state.py
from __future__ import annotations

from dataclasses import dataclass, field

from .types import Message


@dataclass
class RunState:
    run_id: str
    messages: list[Message] = field(default_factory=list)
    step: int = 0

    def append(self, message: Message) -> None:
        self.messages.append(message)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_state.py -v`
预期：1 passed

- [ ] **步骤 5：Commit**

```bash
git add src/harness/state.py tests/test_state.py
git commit -m "feat: RunState 会话状态"
```

---

## 任务 4：配置 `config.py`

**文件：**
- 创建：`src/harness/config.py`
- 测试：`tests/test_config.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_config.py
from harness.config import HarnessConfig


def test_defaults():
    cfg = HarnessConfig(api_key="k")
    assert cfg.model == "gpt-4o-mini"
    assert cfg.max_steps == 10
    assert cfg.base_url.endswith("/v1")


def test_env_override(monkeypatch):
    monkeypatch.setenv("HARNESS_MODEL", "deepseek-chat")
    monkeypatch.setenv("HARNESS_MAX_STEPS", "3")
    cfg = HarnessConfig(api_key="k")
    assert cfg.model == "deepseek-chat"
    assert cfg.max_steps == 3
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_config.py -v`
预期：FAIL，`ModuleNotFoundError: No module named 'harness.config'`

- [ ] **步骤 3：编写实现**

```python
# src/harness/config.py
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class HarnessConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HARNESS_",
        env_file=".env",
        extra="ignore",
        protected_namespaces=(),
    )

    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    system_prompt: str = "You are a helpful assistant."
    max_steps: int = 10
    temperature: float = 0.7
    request_timeout: float = 60.0
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_config.py -v`
预期：2 passed

- [ ] **步骤 5：Commit**

```bash
git add src/harness/config.py tests/test_config.py
git commit -m "feat: HarnessConfig 配置"
```

---

## 任务 5：模型抽象接口 `llm/base.py`

**文件：**
- 创建：`src/harness/llm/base.py`
- 测试：`tests/test_llm_base.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_llm_base.py
from harness.llm.base import StreamChunk, ToolCallDelta, ModelClient


def test_stream_chunk_text():
    c = StreamChunk(type="text", text="hello")
    assert c.type == "text"
    assert c.text == "hello"
    assert c.tool_call_delta is None


def test_stream_chunk_tool_call():
    d = ToolCallDelta(index=0, id="c1", name="calculator", arguments='{"exp')
    c = StreamChunk(type="tool_call", tool_call_delta=d)
    assert c.tool_call_delta.name == "calculator"
    assert c.tool_call_delta.arguments == '{"exp'


def test_model_client_is_protocol():
    # Protocol 的替代验证：接口方法存在
    assert hasattr(ModelClient, "stream")
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_llm_base.py -v`
预期：FAIL，`ModuleNotFoundError: No module named 'harness.llm.base'`

- [ ] **步骤 3：编写实现**

```python
# src/harness/llm/base.py
from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol, runtime_checkable

from ..types import Message


@dataclass
class ToolCallDelta:
    index: int
    id: str | None = None
    name: str | None = None
    arguments: str | None = None  # 部分 JSON 字符串片段，跨 chunk 累加


@dataclass
class StreamChunk:
    type: str  # "text" | "tool_call" | "done"
    text: str | None = None
    tool_call_delta: ToolCallDelta | None = None


@runtime_checkable
class ModelClient(Protocol):
    async def stream(
        self, messages: list[Message], tools: list[dict]
    ) -> AsyncIterator[StreamChunk]:
        ...
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_llm_base.py -v`
预期：3 passed

- [ ] **步骤 5：Commit**

```bash
git add src/harness/llm/base.py tests/test_llm_base.py
git commit -m "feat: ModelClient 协议与 StreamChunk"
```

---

## 任务 6：测试替身 `MockModelClient`（conftest）

**文件：**
- 创建：`tests/conftest.py`
- 测试：`tests/test_conftest_mock.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_conftest_mock.py
import pytest
from harness.llm.base import StreamChunk


async def test_mock_yields_scripted_turns(make_mock, text_turn, tool_turn):
    client = make_mock([
        tool_turn("calculator", '{"expression": "1+1"}', call_id="c1"),
        text_turn("答案是 2"),
    ])
    # 第一轮：工具调用
    chunks = [c async for c in client.stream([], [])]
    assert any(c.type == "tool_call" for c in chunks)
    # 第二轮：纯文本
    chunks2 = [c async for c in client.stream([], [])]
    assert "".join(c.text for c in chunks2 if c.type == "text") == "答案是 2"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_conftest_mock.py -v`
预期：FAIL，fixture `make_mock` 未定义。

- [ ] **步骤 3：编写实现**

```python
# tests/conftest.py
from __future__ import annotations

import pytest

from harness.llm.base import StreamChunk, ToolCallDelta


class MockModelClient:
    """脚本化的 ModelClient 测试替身。

    turns：一个列表，每个元素是"一轮"要 yield 的 StreamChunk 列表。
    每次调用 stream() 消费下一轮。
    """

    def __init__(self, turns: list[list[StreamChunk]]):
        self._turns = list(turns)
        self._i = 0

    async def stream(self, messages, tools):
        turn = self._turns[self._i]
        self._i += 1
        for chunk in turn:
            yield chunk


def _text_turn(text: str) -> list[StreamChunk]:
    # 拆成两个 chunk，模拟流式增量
    mid = max(1, len(text) // 2)
    return [
        StreamChunk(type="text", text=text[:mid]),
        StreamChunk(type="text", text=text[mid:]),
        StreamChunk(type="done"),
    ]


def _tool_turn(name: str, arguments_json: str, call_id: str = "c1") -> list[StreamChunk]:
    # 参数分两段发，验证累加逻辑
    mid = max(1, len(arguments_json) // 2)
    return [
        StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
            index=0, id=call_id, name=name, arguments=arguments_json[:mid])),
        StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
            index=0, arguments=arguments_json[mid:])),
        StreamChunk(type="done"),
    ]


@pytest.fixture
def make_mock():
    return lambda turns: MockModelClient(turns)


@pytest.fixture
def text_turn():
    return _text_turn


@pytest.fixture
def tool_turn():
    return _tool_turn
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_conftest_mock.py -v`
预期：1 passed

- [ ] **步骤 5：Commit**

```bash
git add tests/conftest.py tests/test_conftest_mock.py
git commit -m "test: MockModelClient 测试替身与辅助 fixture"
```

---

## 任务 7：工具系统 `tools/base.py`

**文件：**
- 创建：`src/harness/tools/base.py`
- 测试：`tests/test_tools.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_tools.py
import pytest
from pydantic import BaseModel

from harness.tools.base import Tool, ToolRegistry, ToolExecutor
from harness.types import ToolCall


class EchoTool(Tool):
    name = "echo"
    description = "回显文本"

    class Params(BaseModel):
        text: str

    async def run(self, params) -> str:
        return params.text


def test_schema_shape():
    schema = EchoTool().schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "echo"
    assert "text" in schema["function"]["parameters"]["properties"]


def test_registry_register_and_schemas():
    reg = ToolRegistry()
    reg.register(EchoTool())
    assert reg.get("echo") is not None
    assert reg.get("missing") is None
    assert reg.schemas()[0]["function"]["name"] == "echo"


async def test_executor_success():
    reg = ToolRegistry()
    reg.register(EchoTool())
    ex = ToolExecutor(reg)
    result = await ex.execute(ToolCall(id="c1", name="echo", arguments={"text": "hi"}))
    assert result.content == "hi"
    assert result.is_error is False
    assert result.tool_call_id == "c1"


async def test_executor_unknown_tool():
    ex = ToolExecutor(ToolRegistry())
    result = await ex.execute(ToolCall(id="c1", name="nope", arguments={}))
    assert result.is_error is True
    assert "nope" in result.content


async def test_executor_param_validation_error_feeds_back():
    reg = ToolRegistry()
    reg.register(EchoTool())
    ex = ToolExecutor(reg)
    # 缺少必填 text
    result = await ex.execute(ToolCall(id="c1", name="echo", arguments={}))
    assert result.is_error is True
    assert result.tool_call_id == "c1"


async def test_executor_run_exception_wrapped():
    class BoomTool(Tool):
        name = "boom"
        description = "总是抛错"

        class Params(BaseModel):
            pass

        async def run(self, params) -> str:
            raise RuntimeError("kaboom")

    reg = ToolRegistry()
    reg.register(BoomTool())
    ex = ToolExecutor(reg)
    result = await ex.execute(ToolCall(id="c1", name="boom", arguments={}))
    assert result.is_error is True
    assert "kaboom" in result.content
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_tools.py -v`
预期：FAIL，`ModuleNotFoundError: No module named 'harness.tools.base'`

- [ ] **步骤 3：编写实现**

```python
# src/harness/tools/base.py
from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, ValidationError

from ..types import ToolCall, ToolResult


class Tool(ABC):
    name: str
    description: str
    Params: type[BaseModel]

    @abstractmethod
    async def run(self, params: BaseModel) -> str:
        ...

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.Params.model_json_schema(),
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]


class ToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(self, call: ToolCall) -> ToolResult:
        tool = self._registry.get(call.name)
        if tool is None:
            return ToolResult(call.id, f"未知工具: {call.name}", is_error=True)
        try:
            params = tool.Params(**call.arguments)
        except ValidationError as e:
            return ToolResult(call.id, f"参数校验失败: {e}", is_error=True)
        try:
            content = await tool.run(params)
            return ToolResult(call.id, content, is_error=False)
        except Exception as e:  # 工具内部异常兜成 is_error，喂回模型自纠正
            return ToolResult(call.id, f"工具执行出错: {e}", is_error=True)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_tools.py -v`
预期：6 passed

- [ ] **步骤 5：Commit**

```bash
git add src/harness/tools/base.py tests/test_tools.py
git commit -m "feat: 工具系统 Tool/ToolRegistry/ToolExecutor"
```

---

## 任务 8：玩具工具 `CalculatorTool`

**文件：**
- 创建：`src/harness/tools/builtins/calculator.py`
- 测试：`tests/test_calculator.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_calculator.py
import pytest

from harness.tools.builtins.calculator import CalculatorTool, safe_eval


def test_safe_eval_basic():
    assert safe_eval("(12+8)*3") == 60
    assert safe_eval("2**3") == 8
    assert safe_eval("-5 + 2") == -3


def test_safe_eval_rejects_code():
    with pytest.raises(ValueError):
        safe_eval("__import__('os').system('ls')")
    with pytest.raises(ValueError):
        safe_eval("open('x')")


async def test_calculator_tool_run():
    tool = CalculatorTool()
    result = await tool.run(tool.Params(expression="(12+8)*3"))
    assert result == "60"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_calculator.py -v`
预期：FAIL，`ModuleNotFoundError`

- [ ] **步骤 3：编写实现**

```python
# src/harness/tools/builtins/calculator.py
from __future__ import annotations

import ast
import operator

from pydantic import BaseModel

from ..base import Tool

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval(node: ast.AST):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("不支持的表达式（仅允许数字与 + - * / ** % 和括号）")


def safe_eval(expression: str):
    """受限 AST 求值，绝不使用 eval，杜绝任意代码执行。"""
    return _eval(ast.parse(expression, mode="eval").body)


class CalculatorTool(Tool):
    name = "calculator"
    description = "计算一个算术表达式，支持 + - * / ** % 和括号。"

    class Params(BaseModel):
        expression: str

    async def run(self, params: "CalculatorTool.Params") -> str:
        return str(safe_eval(params.expression))
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_calculator.py -v`
预期：3 passed

- [ ] **步骤 5：Commit**

```bash
git add src/harness/tools/builtins/calculator.py tests/test_calculator.py
git commit -m "feat: CalculatorTool 玩具工具（受限 AST 求值）"
```

---

## 任务 9：上下文管理 `context/manager.py`

**文件：**
- 创建：`src/harness/context/manager.py`
- 测试：`tests/test_context.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_context.py
from harness.context.manager import ContextManager
from harness.state import RunState
from harness.types import Message, Role


def test_build_prepends_system_prompt():
    cm = ContextManager(system_prompt="你是助手")
    state = RunState(run_id="r1")
    state.append(Message(role=Role.USER, content="hi"))
    built = cm.build(state)
    assert built[0].role == Role.SYSTEM
    assert built[0].content == "你是助手"
    assert built[1].content == "hi"
    assert len(built) == 2


def test_build_is_pure_does_not_mutate_state():
    cm = ContextManager(system_prompt="s")
    state = RunState(run_id="r1")
    state.append(Message(role=Role.USER, content="hi"))
    cm.build(state)
    assert len(state.messages) == 1  # 未被污染
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_context.py -v`
预期：FAIL，`ModuleNotFoundError`

- [ ] **步骤 3：编写实现**

```python
# src/harness/context/manager.py
from __future__ import annotations

from ..state import RunState
from ..types import Message, Role


class ContextManager:
    """决定每轮发给模型的消息列表。

    v1 极简：system_prompt + 完整历史。build() 是纯函数，
    未来的裁剪/压缩/RAG 注入都在这里加，loop 无感知。
    """

    def __init__(self, system_prompt: str) -> None:
        self._system_prompt = system_prompt

    def build(self, state: RunState) -> list[Message]:
        return [Message(role=Role.SYSTEM, content=self._system_prompt), *state.messages]
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_context.py -v`
预期：2 passed

- [ ] **步骤 5：Commit**

```bash
git add src/harness/context/manager.py tests/test_context.py
git commit -m "feat: ContextManager 上下文组装"
```

---

## 任务 10：编排内核 `loop/agent_loop.py`（核心）

**文件：**
- 创建：`src/harness/loop/agent_loop.py`
- 测试：`tests/test_loop.py`

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_loop.py
import pytest

from harness.loop.agent_loop import AgentLoop
from harness.context.manager import ContextManager
from harness.tools.base import ToolRegistry
from harness.tools.builtins.calculator import CalculatorTool
from harness.events import (
    RunStarted, TextDelta, ToolCallRequested, ToolStarted, ToolFinished,
    RunFinished, RunError, StepStarted,
)


def _build_loop(client, max_steps=10):
    reg = ToolRegistry()
    reg.register(CalculatorTool())
    ctx = ContextManager(system_prompt="s")
    return AgentLoop(
        client=client, registry=reg, context=ctx,
        max_steps=max_steps, run_id_factory=lambda: "run-test",
    )


async def _collect(loop, msg):
    return [ev async for ev in loop.run(msg)]


async def test_plain_chat_terminates_without_tools(make_mock, text_turn):
    loop = _build_loop(make_mock([text_turn("你好呀")]))
    events = await _collect(loop, "hi")
    assert isinstance(events[0], RunStarted)
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "你好呀"
    assert isinstance(events[-1], RunFinished)
    assert events[-1].message.content == "你好呀"
    # 未触发任何工具
    assert not any(isinstance(e, ToolStarted) for e in events)


async def test_tool_call_executes_and_feeds_back(make_mock, text_turn, tool_turn):
    client = make_mock([
        tool_turn("calculator", '{"expression": "(12+8)*3"}', call_id="c1"),
        text_turn("答案是 60"),
    ])
    loop = _build_loop(client)
    events = await _collect(loop, "算 (12+8)*3")
    assert any(isinstance(e, ToolCallRequested) for e in events)
    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert len(finished) == 1
    assert finished[0].result.content == "60"
    assert finished[0].result.is_error is False
    assert isinstance(events[-1], RunFinished)
    assert events[-1].message.content == "答案是 60"


async def test_max_steps_guard_emits_run_error(make_mock, tool_turn):
    # 每轮都请求工具，永不给最终答案 → 应在 max_steps 后 RunError
    turns = [tool_turn("calculator", '{"expression": "1+1"}', call_id=f"c{i}")
             for i in range(5)]
    loop = _build_loop(make_mock(turns), max_steps=2)
    events = await _collect(loop, "loop forever")
    assert isinstance(events[-1], RunError)
    assert "max_steps" in events[-1].error
    # 恰好 2 个 StepStarted
    assert sum(isinstance(e, StepStarted) for e in events) == 2


async def test_llm_stream_exception_becomes_run_error(text_turn):
    class BoomClient:
        async def stream(self, messages, tools):
            raise ConnectionError("network down")
            yield  # pragma: no cover  (使其成为 async generator)

    loop = _build_loop(BoomClient())
    events = await _collect(loop, "hi")
    assert isinstance(events[-1], RunError)
    assert "network down" in events[-1].error


async def test_bad_tool_args_feed_back_is_error(make_mock, text_turn, tool_turn):
    # 参数缺 expression → executor 返回 is_error，loop 照常回填并继续
    client = make_mock([
        tool_turn("calculator", '{}', call_id="c1"),
        text_turn("我需要一个表达式"),
    ])
    loop = _build_loop(client)
    events = await _collect(loop, "算点啥")
    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert finished[0].result.is_error is True
    assert isinstance(events[-1], RunFinished)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_loop.py -v`
预期：FAIL，`ModuleNotFoundError: No module named 'harness.loop.agent_loop'`

- [ ] **步骤 3：编写实现**

```python
# src/harness/loop/agent_loop.py
from __future__ import annotations

import json
import uuid
from typing import AsyncIterator, Callable

from ..context.manager import ContextManager
from ..events import (
    Event, RunStarted, StepStarted, TextDelta, ToolCallRequested,
    ToolStarted, ToolFinished, StepFinished, RunFinished, RunError,
)
from ..llm.base import ModelClient, ToolCallDelta
from ..state import RunState
from ..tools.base import ToolExecutor, ToolRegistry
from ..types import Message, Role, ToolCall


def _accumulate(acc: dict[int, dict], delta: ToolCallDelta) -> None:
    slot = acc.setdefault(delta.index, {"id": None, "name": None, "args": ""})
    if delta.id:
        slot["id"] = delta.id
    if delta.name:
        slot["name"] = delta.name
    if delta.arguments:
        slot["args"] += delta.arguments


def _finalize(acc: dict[int, dict]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for idx in sorted(acc):
        slot = acc[idx]
        try:
            args = json.loads(slot["args"]) if slot["args"] else {}
        except json.JSONDecodeError:
            args = {}
        calls.append(ToolCall(
            id=slot["id"] or f"call_{idx}",
            name=slot["name"] or "",
            arguments=args,
        ))
    return calls


class AgentLoop:
    def __init__(
        self,
        client: ModelClient,
        registry: ToolRegistry,
        context: ContextManager,
        max_steps: int = 10,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._client = client
        self._registry = registry
        self._executor = ToolExecutor(registry)
        self._context = context
        self._max_steps = max_steps
        self._new_run_id = run_id_factory or (lambda: uuid.uuid4().hex)

    async def run(self, user_message: str) -> AsyncIterator[Event]:
        state = RunState(run_id=self._new_run_id())
        state.append(Message(role=Role.USER, content=user_message))
        yield RunStarted(run_id=state.run_id)

        for step in range(1, self._max_steps + 1):
            state.step = step
            yield StepStarted(step=step)

            messages = self._context.build(state)
            content_parts: list[str] = []
            tool_acc: dict[int, dict] = {}
            try:
                async for chunk in self._client.stream(messages, self._registry.schemas()):
                    if chunk.type == "text" and chunk.text:
                        content_parts.append(chunk.text)
                        yield TextDelta(text=chunk.text)
                    elif chunk.type == "tool_call" and chunk.tool_call_delta:
                        _accumulate(tool_acc, chunk.tool_call_delta)
            except Exception as e:
                yield RunError(error=f"模型调用失败: {e}")
                return

            tool_calls = _finalize(tool_acc)
            assistant = Message(
                role=Role.ASSISTANT,
                content="".join(content_parts) or None,
                tool_calls=tool_calls,
            )
            state.append(assistant)

            if not tool_calls:  # 终止条件①：模型不再要工具
                yield RunFinished(message=assistant)
                return

            yield ToolCallRequested(tool_calls=tool_calls)
            for tc in tool_calls:  # v1 顺序执行
                yield ToolStarted(tool_call=tc)
                result = await self._executor.execute(tc)
                state.append(Message(
                    role=Role.TOOL, content=result.content, tool_call_id=tc.id))
                yield ToolFinished(result=result)
            yield StepFinished(step=step)

        yield RunError(error=f"达到 max_steps 上限 ({self._max_steps})")
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_loop.py -v`
预期：5 passed

- [ ] **步骤 5：运行全部测试确认无回归**

运行：`uv run pytest -v`
预期：全部 passed

- [ ] **步骤 6：Commit**

```bash
git add src/harness/loop/agent_loop.py tests/test_loop.py
git commit -m "feat: AgentLoop 编排内核（事件流+终止+工具回填）"
```

---

## 任务 11：真实端点实现 `llm/openai_compat.py`

**文件：**
- 创建：`src/harness/llm/openai_compat.py`
- 测试：`tests/test_openai_compat.py`（用 fake stream，不打网络）

- [ ] **步骤 1：编写失败的测试**

```python
# tests/test_openai_compat.py
import pytest

from harness.llm.openai_compat import OpenAICompatibleClient
from harness.config import HarnessConfig
from harness.types import Message, Role


class _FakeDelta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, delta):
        self.delta = delta


class _FakeEvent:
    def __init__(self, delta):
        self.choices = [_FakeChoice(delta)]


class _FakeToolCall:
    def __init__(self, index, id, name, arguments):
        self.index = index
        self.id = id
        self.function = type("F", (), {"name": name, "arguments": arguments})()


async def _fake_stream(events):
    for e in events:
        yield e


async def test_stream_normalizes_text_and_tool_and_done(monkeypatch):
    cfg = HarnessConfig(api_key="k")
    client = OpenAICompatibleClient(cfg)

    events = [
        _FakeEvent(_FakeDelta(content="你好")),
        _FakeEvent(_FakeDelta(tool_calls=[_FakeToolCall(0, "c1", "calculator", '{"e')])),
        _FakeEvent(_FakeDelta(tool_calls=[_FakeToolCall(0, None, None, 'xp": "1+1"}')])),
    ]

    async def fake_create(**kwargs):
        assert kwargs["stream"] is True
        assert kwargs["messages"][0]["role"] == "user"
        return _fake_stream(events)

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)

    out = [c async for c in client.stream([Message(role=Role.USER, content="hi")], [])]
    assert out[0].type == "text" and out[0].text == "你好"
    assert out[1].type == "tool_call" and out[1].tool_call_delta.name == "calculator"
    assert out[2].tool_call_delta.arguments == 'xp": "1+1"}'
    assert out[-1].type == "done"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_openai_compat.py -v`
预期：FAIL，`ModuleNotFoundError`

- [ ] **步骤 3：编写实现**

```python
# src/harness/llm/openai_compat.py
from __future__ import annotations

from typing import AsyncIterator

from openai import AsyncOpenAI

from ..config import HarnessConfig
from ..types import Message
from .base import StreamChunk, ToolCallDelta


class OpenAICompatibleClient:
    """基于 openai async SDK 的实现，base_url 可指向任意兼容端点。

    职责：把"消息列表 + 工具 schema"变成归一化的 StreamChunk 流。
    不做重试、不做路由。
    """

    def __init__(self, config: HarnessConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.request_timeout,
        )

    async def stream(
        self, messages: list[Message], tools: list[dict]
    ) -> AsyncIterator[StreamChunk]:
        kwargs: dict = {
            "model": self._config.model,
            "messages": [m.to_openai() for m in messages],
            "temperature": self._config.temperature,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        stream = await self._client.chat.completions.create(**kwargs)
        async for event in stream:
            if not event.choices:
                continue
            delta = event.choices[0].delta
            if getattr(delta, "content", None):
                yield StreamChunk(type="text", text=delta.content)
            for tc in (getattr(delta, "tool_calls", None) or []):
                fn = getattr(tc, "function", None)
                yield StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
                    index=tc.index,
                    id=getattr(tc, "id", None),
                    name=getattr(fn, "name", None) if fn else None,
                    arguments=getattr(fn, "arguments", None) if fn else None,
                ))
        yield StreamChunk(type="done")
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_openai_compat.py -v`
预期：1 passed

- [ ] **步骤 5：Commit**

```bash
git add src/harness/llm/openai_compat.py tests/test_openai_compat.py
git commit -m "feat: OpenAICompatibleClient 真实端点实现"
```

---

## 任务 12：Demo 脚本与端到端验收

**文件：**
- 创建：`examples/demo.py`

- [ ] **步骤 1：编写 demo 脚本**

```python
# examples/demo.py
"""订阅事件流逐条打印。需要 .env 配好 HARNESS_API_KEY 等。

运行：uv run python examples/demo.py "帮我算 (12+8)*3"
"""
from __future__ import annotations

import asyncio
import sys

from harness.config import HarnessConfig
from harness.context.manager import ContextManager
from harness.events import (
    RunStarted, StepStarted, TextDelta, ToolCallRequested,
    ToolStarted, ToolFinished, StepFinished, RunFinished, RunError,
)
from harness.llm.openai_compat import OpenAICompatibleClient
from harness.loop.agent_loop import AgentLoop
from harness.tools.base import ToolRegistry
from harness.tools.builtins.calculator import CalculatorTool


async def main(user_message: str) -> None:
    cfg = HarnessConfig()
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    loop = AgentLoop(
        client=OpenAICompatibleClient(cfg),
        registry=registry,
        context=ContextManager(system_prompt=cfg.system_prompt),
        max_steps=cfg.max_steps,
    )

    async for ev in loop.run(user_message):
        if isinstance(ev, RunStarted):
            print(f"\n[run {ev.run_id}]")
        elif isinstance(ev, StepStarted):
            print(f"\n--- step {ev.step} ---")
        elif isinstance(ev, TextDelta):
            print(ev.text, end="", flush=True)
        elif isinstance(ev, ToolCallRequested):
            print(f"\n[请求工具] {[tc.name for tc in ev.tool_calls]}")
        elif isinstance(ev, ToolStarted):
            print(f"[执行] {ev.tool_call.name}({ev.tool_call.arguments})")
        elif isinstance(ev, ToolFinished):
            flag = "ERR" if ev.result.is_error else "OK"
            print(f"[结果:{flag}] {ev.result.content}")
        elif isinstance(ev, StepFinished):
            pass
        elif isinstance(ev, RunFinished):
            print(f"\n\n[完成] {ev.message.content}")
        elif isinstance(ev, RunError):
            print(f"\n\n[出错] {ev.error}")


if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else "帮我算 (12+8)*3"
    asyncio.run(main(msg))
```

- [ ] **步骤 2：全部单测通过**

运行：`uv run pytest -v`
预期：全部 passed（约 25+ 项）

- [ ] **步骤 3：真实端点手动验收（需配 `.env`）**

先 `cp .env.example .env` 并填入真实 key/base_url/model，然后：
```bash
uv run python examples/demo.py "帮我算 (12+8)*3"
```
预期：看到 `[请求工具] ['calculator']` → `[结果:OK] 60` → `[完成] ...60...`

再验证纯闲聊不触发工具：
```bash
uv run python examples/demo.py "你好，简单介绍下你自己"
```
预期：直接流式输出文本，无 `[请求工具]`。

- [ ] **步骤 4：Commit**

```bash
git add examples/demo.py
git commit -m "feat: demo 脚本与端到端验收"
```

---

## 完成标准（对照规格验收）

- [ ] 计算类问题触发 `calculator` 并回填结果作答（任务 10 测试 + 任务 12 手动）
- [ ] 纯闲聊不触发工具、直接流式作答（任务 10 测试 + 任务 12 手动）
- [ ] demo 能订阅事件流逐条打印（任务 12）
- [ ] `max_steps` 正确终止防死循环（任务 10 测试）
- [ ] MockModelClient 覆盖 loop 所有分支、不打真实 API（任务 6/10）
- [ ] `uv run pytest` 全绿

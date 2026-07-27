> ⚠️ **历史设计记录（已过时）**：harness 内核已抽成外部包
> [ai-harness-framework](https://github.com/sumengnan/ai-harness-framework)（import 名仍是 `harness`）。
> 本文是带日期的设计存档，文中的 `src/harness/` 路径与打包配置反映**当时**的仓库结构、未随抽包更新；
> 当前结构以 [架构文档](../../architecture-harness.md) 为准。

# AI Harness 可靠性层（子项目②）实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在①内核之上加可靠性层——OpenTelemetry 可观测性 + 传输层重试/循环层自纠正 + 资源上限（token/时间预算、工具结果截断、calculator 幂运算守卫）。

**架构：** 新增 `telemetry/`（OTel 注入式插桩，默认 no-op）、`reliability/`（`RetryingModelClient` 装饰器 + `BudgetTracker`）、`usage.py`（token 计数+成本）；改造 `llm`（StreamChunk 带 usage/attempts、client 请求真实 usage）、`loop`（注入 tracer+budget、步边界查预算、span 包裹、自纠正回填、发 ModelUsage 事件）、`tools`/`calculator`/`config`/`events`。事件流仍只服务 UI。

**技术栈：** 沿用① · 新增 `opentelemetry-api`/`opentelemetry-sdk`/`tiktoken`。

**规格：** `docs/superpowers/specs/2026-07-08-harness-reliability-design.md`

**提交规范：** git 身份已是 sumengnan，直接默认提交；**任何 commit message 不得出现 Claude/AI/Co-Authored-By 等署名**。

---

## 文件结构与职责

| 文件 | 动作 | 职责 |
|---|---|---|
| `src/harness/usage.py` | 新增 | `Usage` 数据类、`estimate_usage`（tiktoken 兜底）、`cost_usd` |
| `src/harness/reliability/budget.py` | 新增 | `BudgetTracker`（token/时间累计+`check`）、`BudgetExceeded` |
| `src/harness/reliability/retry.py` | 新增 | `RetryingModelClient`（装饰 ModelClient，指数退避） |
| `src/harness/telemetry/tracer.py` | 新增 | `get_tracer()`（默认 no-op）、`setup_telemetry()` |
| `src/harness/llm/base.py` | 改 | `StreamChunk` 增 `usage`/`attempts` |
| `src/harness/events.py` | 改 | 新增 `ModelUsage` 事件 |
| `src/harness/llm/openai_compat.py` | 改 | 请求带 `stream_options.include_usage`；done 吐 usage（无则 tiktoken 兜底） |
| `src/harness/tools/base.py` | 改 | 工具结果超长截断 |
| `src/harness/tools/builtins/calculator.py` | 改 | 幂运算量级守卫 |
| `src/harness/loop/agent_loop.py` | 改 | 注入 tracer+budget、步边界查预算、span、自纠正回填、发 ModelUsage |
| `src/harness/config.py` | 改 | 新增预算/重试/OTel/价格表配置 |
| `tests/conftest.py` | 改 | 新增 `FlakyModelClient` + 带 usage 的 turn 辅助 |
| `pyproject.toml` | 改 | 新增依赖 |

---

## 任务 0：依赖与配置

**文件：** 改 `pyproject.toml`、`src/harness/config.py`、测试 `tests/test_config.py`

- [ ] **步骤 1：`pyproject.toml` 的 `dependencies` 追加**

```toml
    "opentelemetry-api>=1.25",
    "opentelemetry-sdk>=1.25",
    "tiktoken>=0.7",
```

- [ ] **步骤 2：`uv sync` 安装**

运行：`uv sync`　预期：成功装上 opentelemetry / tiktoken。

- [ ] **步骤 3：先写失败测试（config 新字段）**

在 `tests/test_config.py` 追加：

```python
def test_reliability_defaults():
    cfg = HarnessConfig(api_key="k")
    assert cfg.max_retries == 2
    assert cfg.retry_base_delay == 0.5
    assert cfg.max_tokens_budget is None
    assert cfg.max_wall_seconds is None
    assert cfg.tool_result_max_chars == 8000
    assert cfg.otel_enabled is False
    assert cfg.otel_exporter == "console"
    assert cfg.price_map == {}
```

运行：`uv run pytest tests/test_config.py::test_reliability_defaults -v`　预期：FAIL。

- [ ] **步骤 4：改 `src/harness/config.py`**，在 `request_timeout` 后追加字段：

```python
    max_retries: int = 2
    retry_base_delay: float = 0.5
    max_tokens_budget: int | None = None
    max_wall_seconds: float | None = None
    tool_result_max_chars: int = 8000
    otel_enabled: bool = False
    otel_exporter: str = "console"      # console | otlp
    otel_endpoint: str = ""
    price_map: dict = {}                 # {model: [in_per_1k, out_per_1k]}
```

- [ ] **步骤 5：跑通并 commit**

运行：`uv run pytest tests/test_config.py -v`　预期：全 pass。
```bash
git add pyproject.toml uv.lock src/harness/config.py tests/test_config.py
git commit -m "chore: 可靠性层依赖与配置项"
```

---

## 任务 1：token 计数与成本 `usage.py`

**文件：** 创建 `src/harness/usage.py`、测试 `tests/test_usage.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/test_usage.py
from harness.usage import Usage, estimate_usage, cost_usd
from harness.types import Message, Role


def test_usage_add():
    a = Usage(1, 2, 3)
    b = Usage(10, 20, 30)
    c = a + b
    assert (c.prompt_tokens, c.completion_tokens, c.total_tokens) == (11, 22, 33)


def test_estimate_usage_nonzero():
    u = estimate_usage([Message(role=Role.USER, content="hello world")], "hi there", "gpt-4o-mini")
    assert u.prompt_tokens > 0
    assert u.completion_tokens > 0
    assert u.total_tokens == u.prompt_tokens + u.completion_tokens


def test_cost_usd_with_price():
    u = Usage(1000, 1000, 2000)
    assert cost_usd(u, "m", {"m": [1.0, 2.0]}) == 3.0


def test_cost_usd_without_price_is_none():
    assert cost_usd(Usage(1, 1, 2), "m", {}) is None
```

运行：`uv run pytest tests/test_usage.py -v`　预期：FAIL（模块不存在）。

- [ ] **步骤 2：实现 `src/harness/usage.py`**

```python
# src/harness/usage.py
from __future__ import annotations

from dataclasses import dataclass

from .types import Message


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.total_tokens + other.total_tokens,
        )


def _encode_len(text: str, model: str) -> int:
    import tiktoken

    try:
        enc = tiktoken.encoding_for_model(model)
    except KeyError:
        enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(text))


def estimate_usage(messages: list[Message], completion: str, model: str) -> Usage:
    """端点不返回真实 usage 时的 tiktoken 估算（近似值，仅用于预算保护）。"""
    prompt_text = "\n".join(m.content or "" for m in messages)
    p = _encode_len(prompt_text, model)
    c = _encode_len(completion, model)
    return Usage(prompt_tokens=p, completion_tokens=c, total_tokens=p + c)


def cost_usd(usage: Usage, model: str, price_map: dict) -> float | None:
    price = price_map.get(model)
    if not price:
        return None
    in_per_1k, out_per_1k = price
    return usage.prompt_tokens / 1000 * in_per_1k + usage.completion_tokens / 1000 * out_per_1k
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/test_usage.py -v`　预期：4 passed。
```bash
git add src/harness/usage.py tests/test_usage.py
git commit -m "feat: usage token 计数与成本估算"
```

---

## 任务 2：StreamChunk 扩展 + ModelUsage 事件 + conftest 辅助

**文件：** 改 `src/harness/llm/base.py`、`src/harness/events.py`、`tests/conftest.py`；测试 `tests/test_llm_base.py`、`tests/test_events.py`

- [ ] **步骤 1：写失败测试**

`tests/test_llm_base.py` 追加：
```python
def test_stream_chunk_done_carries_usage_and_attempts():
    from harness.usage import Usage
    c = StreamChunk(type="done", usage=Usage(1, 2, 3), attempts=2)
    assert c.usage.total_tokens == 3
    assert c.attempts == 2


def test_stream_chunk_defaults_attempts_one():
    assert StreamChunk(type="text", text="x").attempts == 1
```

`tests/test_events.py` 追加：
```python
def test_model_usage_event():
    from harness.events import ModelUsage
    from harness.usage import Usage
    ev = ModelUsage(usage=Usage(1, 2, 3), cost_usd=0.5, attempts=1, latency_ms=12.0)
    assert ev.usage.total_tokens == 3
    assert ev.cost_usd == 0.5
    assert ev.attempts == 1
    assert ev.latency_ms == 12.0
```

运行两文件：预期 FAIL。

- [ ] **步骤 2：改 `src/harness/llm/base.py` 的 `StreamChunk`**

在文件顶部 import 追加 `from ..usage import Usage`，并把 `StreamChunk` 改为：
```python
@dataclass
class StreamChunk:
    type: str  # "text" | "tool_call" | "done"
    text: str | None = None
    tool_call_delta: ToolCallDelta | None = None
    usage: Usage | None = None      # 仅 done chunk 携带
    attempts: int = 1               # 仅 done chunk 携带（重试次数）
```

- [ ] **步骤 3：改 `src/harness/events.py`**，文件顶部 import 追加 `from .usage import Usage`，末尾追加：
```python
@dataclass
class ModelUsage(Event):
    usage: Usage
    cost_usd: float | None
    attempts: int
    latency_ms: float
```

- [ ] **步骤 4：改 `tests/conftest.py`** 追加带 usage 的 turn 辅助与 fixture（供后续预算/loop 测试用）：

```python
from harness.usage import Usage


def _done_with_usage(prompt=10, completion=5, attempts=1):
    return StreamChunk(type="done", usage=Usage(prompt, completion, prompt + completion), attempts=attempts)


def _text_turn_usage(text: str, prompt=10, completion=5):
    return [StreamChunk(type="text", text=text), _done_with_usage(prompt, completion)]


@pytest.fixture
def done_with_usage():
    return _done_with_usage


@pytest.fixture
def text_turn_usage():
    return _text_turn_usage
```

- [ ] **步骤 5：跑通并 commit**

运行：`uv run pytest tests/test_llm_base.py tests/test_events.py -v`　预期全 pass；再 `uv run pytest -q` 确认无回归。
```bash
git add src/harness/llm/base.py src/harness/events.py tests/conftest.py tests/test_llm_base.py tests/test_events.py
git commit -m "feat: StreamChunk 带 usage/attempts + ModelUsage 事件"
```

---

## 任务 3：预算 `reliability/budget.py`

**文件：** 创建 `src/harness/reliability/__init__.py`、`src/harness/reliability/budget.py`、测试 `tests/test_budget.py`

- [ ] **步骤 1：建包**

运行：`touch src/harness/reliability/__init__.py`

- [ ] **步骤 2：写失败测试**

```python
# tests/test_budget.py
import pytest

from harness.reliability.budget import BudgetTracker, BudgetExceeded
from harness.usage import Usage


def test_no_limits_never_raises():
    b = BudgetTracker()
    b.start()
    b.add_usage(Usage(0, 0, 10**9))
    b.check()  # 不抛


def test_token_budget_exceeded():
    b = BudgetTracker(max_tokens=100)
    b.start()
    b.add_usage(Usage(60, 60, 120))
    with pytest.raises(BudgetExceeded):
        b.check()


def test_time_budget_exceeded_with_fake_clock():
    ticks = iter([0.0, 0.0, 5.0])  # start, (add), check
    b = BudgetTracker(max_wall_seconds=3.0, clock=lambda: next(ticks))
    b.start()
    with pytest.raises(BudgetExceeded):
        b.check()


def test_total_tokens_accumulates():
    b = BudgetTracker()
    b.start()
    b.add_usage(Usage(1, 1, 2))
    b.add_usage(Usage(3, 3, 6))
    assert b.total_tokens == 8
```

运行：预期 FAIL。

- [ ] **步骤 3：实现 `src/harness/reliability/budget.py`**

```python
# src/harness/reliability/budget.py
from __future__ import annotations

import time
from typing import Callable

from ..usage import Usage


class BudgetExceeded(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class BudgetTracker:
    """纯累计器：累计 token 与墙钟时间，check() 超限即抛 BudgetExceeded。

    clock 可注入以便测试（默认 time.monotonic）。
    """

    def __init__(
        self,
        max_tokens: int | None = None,
        max_wall_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_tokens = max_tokens
        self._max_wall = max_wall_seconds
        self._clock = clock
        self._start: float | None = None
        self._total_tokens = 0

    def start(self) -> None:
        self._start = self._clock()

    def add_usage(self, usage: Usage) -> None:
        self._total_tokens += usage.total_tokens

    def check(self) -> None:
        if self._max_tokens is not None and self._total_tokens > self._max_tokens:
            raise BudgetExceeded(f"token 预算超限：{self._total_tokens} > {self._max_tokens}")
        if self._max_wall is not None and self._start is not None:
            elapsed = self._clock() - self._start
            if elapsed > self._max_wall:
                raise BudgetExceeded(f"时间预算超限：{elapsed:.1f}s > {self._max_wall}s")

    @property
    def total_tokens(self) -> int:
        return self._total_tokens
```

- [ ] **步骤 4：跑通并 commit**

运行：`uv run pytest tests/test_budget.py -v`　预期：4 passed。
```bash
git add src/harness/reliability/__init__.py src/harness/reliability/budget.py tests/test_budget.py
git commit -m "feat: BudgetTracker token/时间预算"
```

---

## 任务 4：传输层重试 `reliability/retry.py`

**文件：** 创建 `src/harness/reliability/retry.py`、改 `tests/conftest.py`（加 `FlakyModelClient`）、测试 `tests/test_retry.py`

- [ ] **步骤 1：`tests/conftest.py` 追加 `FlakyModelClient` 与 fixture**

```python
class FlakyModelClient:
    """前 fail_times 次调用 stream 抛 transient_exc，之后正常吐 turn。

    mid_stream=True 时改为：先 yield 一个 chunk 再抛（模拟流中途断裂，不可重试）。
    """

    def __init__(self, transient_exc, turn, fail_times=1, mid_stream=False):
        self._exc = transient_exc
        self._turn = turn
        self._fail_times = fail_times
        self._mid_stream = mid_stream
        self.calls = 0

    async def stream(self, messages, tools):
        self.calls += 1
        if self.calls <= self._fail_times:
            if self._mid_stream:
                yield StreamChunk(type="text", text="半截")
            raise self._exc
        for chunk in self._turn:
            yield chunk


@pytest.fixture
def flaky_client():
    return FlakyModelClient
```

- [ ] **步骤 2：写失败测试**

```python
# tests/test_retry.py
import pytest

from harness.reliability.retry import RetryingModelClient
from harness.llm.base import StreamChunk


class Transient(Exception):
    pass


def _sleeps():
    calls = []

    async def fake_sleep(d):
        calls.append(d)

    return calls, fake_sleep


async def test_retries_then_succeeds(flaky_client, text_turn):
    slept, fake_sleep = _sleeps()
    inner = flaky_client(Transient("timeout"), text_turn("ok"), fail_times=2)
    client = RetryingModelClient(inner, max_retries=2, base_delay=0.1,
                                 sleep=fake_sleep, transient=(Transient,))
    chunks = [c async for c in client.stream([], [])]
    assert inner.calls == 3                       # 1 正常 + 2 重试
    assert "".join(c.text for c in chunks if c.type == "text") == "ok"
    assert len(slept) == 2                        # 退避 2 次
    done = [c for c in chunks if c.type == "done"][0]
    assert done.attempts == 3


async def test_exhausts_and_raises(flaky_client, text_turn):
    slept, fake_sleep = _sleeps()
    inner = flaky_client(Transient("timeout"), text_turn("never"), fail_times=99)
    client = RetryingModelClient(inner, max_retries=2, base_delay=0.1,
                                 sleep=fake_sleep, transient=(Transient,))
    with pytest.raises(Transient):
        [c async for c in client.stream([], [])]
    assert inner.calls == 3                       # 1 + 2 后放弃


async def test_mid_stream_break_not_retried(flaky_client, text_turn):
    slept, fake_sleep = _sleeps()
    inner = flaky_client(Transient("mid"), text_turn("ok"), fail_times=1, mid_stream=True)
    client = RetryingModelClient(inner, max_retries=3, base_delay=0.1,
                                 sleep=fake_sleep, transient=(Transient,))
    with pytest.raises(Transient):
        [c async for c in client.stream([], [])]
    assert inner.calls == 1                        # 已产出 chunk，不重试
    assert slept == []
```

运行：预期 FAIL。

- [ ] **步骤 3：实现 `src/harness/reliability/retry.py`**

```python
# src/harness/reliability/retry.py
from __future__ import annotations

import asyncio
import random
from typing import AsyncIterator, Awaitable, Callable

from ..llm.base import ModelClient, StreamChunk
from ..types import Message

try:  # 真实运行时用 openai 的瞬时错误类型
    from openai import (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )

    _DEFAULT_TRANSIENT: tuple[type[BaseException], ...] = (
        APITimeoutError,
        APIConnectionError,
        RateLimitError,
        InternalServerError,
    )
except Exception:  # openai 未安装时的降级（正常环境不会触发）
    _DEFAULT_TRANSIENT = ()


class RetryingModelClient:
    """装饰任意 ModelClient，对瞬时错误做指数退避重试。

    安全约束：只在流尚未产出任何 chunk 前失败才重试；中途断裂直接抛出，
    避免重复 yield 半截输出。仍实现 ModelClient 协议，loop 无感知。
    """

    def __init__(
        self,
        inner: ModelClient,
        max_retries: int = 2,
        base_delay: float = 0.5,
        transient: tuple[type[BaseException], ...] = _DEFAULT_TRANSIENT,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        tracer=None,
    ) -> None:
        self._inner = inner
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._transient = transient
        self._sleep = sleep
        self._tracer = tracer

    async def stream(
        self, messages: list[Message], tools: list[dict]
    ) -> AsyncIterator[StreamChunk]:
        for attempt in range(1, self._max_retries + 2):  # 1 次正常 + max_retries 次重试
            produced = False
            try:
                async for chunk in self._inner.stream(messages, tools):
                    produced = True
                    if chunk.type == "done":
                        chunk.attempts = attempt
                    yield chunk
                return
            except self._transient:
                if produced or attempt > self._max_retries:
                    raise
                delay = self._base_delay * 2 ** (attempt - 1)
                delay += random.uniform(0, self._base_delay * 0.1)  # 抖动
                await self._sleep(delay)
```

- [ ] **步骤 4：跑通并 commit**

运行：`uv run pytest tests/test_retry.py -v`　预期：3 passed。
```bash
git add src/harness/reliability/retry.py tests/conftest.py tests/test_retry.py
git commit -m "feat: RetryingModelClient 传输层指数退避重试"
```

---

## 任务 5：可观测性 `telemetry/tracer.py`

**文件：** 创建 `src/harness/telemetry/__init__.py`、`src/harness/telemetry/tracer.py`、测试 `tests/test_telemetry.py`

- [ ] **步骤 1：建包**

运行：`touch src/harness/telemetry/__init__.py`

- [ ] **步骤 2：写失败测试**

```python
# tests/test_telemetry.py
from harness.telemetry.tracer import get_tracer, setup_telemetry


def test_get_tracer_noop_by_default_does_not_raise():
    tracer = get_tracer()
    with tracer.start_as_current_span("x") as span:
        span.set_attribute("k", "v")   # no-op tracer 也不报错


def test_setup_telemetry_disabled_is_noop():
    class Cfg:
        otel_enabled = False
        otel_exporter = "console"
        otel_endpoint = ""

    # 不抛异常即可
    setup_telemetry(Cfg())
```

运行：预期 FAIL。

- [ ] **步骤 3：实现 `src/harness/telemetry/tracer.py`**

```python
# src/harness/telemetry/tracer.py
from __future__ import annotations

from opentelemetry import trace


def get_tracer(name: str = "harness"):
    """返回 tracer。未配置全局 provider 时 OTel 默认返回 no-op tracer，零开销、不报错。"""
    return trace.get_tracer(name)


def setup_telemetry(config) -> None:
    """按配置安装 OTel provider；otel_enabled=False 时什么都不做（保持 no-op）。"""
    if not getattr(config, "otel_enabled", False):
        return

    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    provider = TracerProvider()
    if config.otel_exporter == "otlp":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(endpoint=config.otel_endpoint or None)
    else:
        exporter = ConsoleSpanExporter()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
```

- [ ] **步骤 4：跑通并 commit**

运行：`uv run pytest tests/test_telemetry.py -v`　预期：2 passed。
```bash
git add src/harness/telemetry/__init__.py src/harness/telemetry/tracer.py tests/test_telemetry.py
git commit -m "feat: telemetry tracer（默认 no-op + 可选 exporter）"
```

---

## 任务 6：工具结果截断

**文件：** 改 `src/harness/tools/base.py`、测试 `tests/test_tools.py`

- [ ] **步骤 1：写失败测试**（`tests/test_tools.py` 追加）

```python
async def test_tool_result_truncated_when_too_long():
    class LongTool(Tool):
        name = "long"
        description = "返回超长文本"

        class Params(BaseModel):
            pass

        async def run(self, params) -> str:
            return "x" * 100

    reg = ToolRegistry()
    reg.register(LongTool())
    ex = ToolExecutor(reg, max_chars=20)
    result = await ex.execute(ToolCall(id="c1", name="long", arguments={}))
    assert len(result.content) <= 20 + len("…(已截断)")
    assert result.content.endswith("…(已截断)")
    assert result.is_error is False
```

运行：预期 FAIL（`ToolExecutor` 无 `max_chars` 参数）。

- [ ] **步骤 2：改 `src/harness/tools/base.py` 的 `ToolExecutor`**

```python
class ToolExecutor:
    def __init__(self, registry: ToolRegistry, max_chars: int | None = None) -> None:
        self._registry = registry
        self._max_chars = max_chars

    def _truncate(self, text: str) -> str:
        if self._max_chars is not None and len(text) > self._max_chars:
            return text[: self._max_chars] + "…(已截断)"
        return text

    async def execute(self, call: ToolCall) -> ToolResult:
        tool = self._registry.get(call.name)
        if tool is None:
            return ToolResult(call.id, f"未知工具: {call.name}", is_error=True)
        try:
            params = tool.Params.model_validate(call.arguments)
        except ValidationError as e:
            return ToolResult(call.id, f"参数校验失败: {e}", is_error=True)
        try:
            content = await tool.run(params)
            return ToolResult(call.id, self._truncate(content), is_error=False)
        except Exception as e:
            return ToolResult(call.id, f"工具执行出错: {e}", is_error=True)
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/test_tools.py -v`　预期：全 pass（含新用例）。
```bash
git add src/harness/tools/base.py tests/test_tools.py
git commit -m "feat: 工具结果超长截断"
```

---

## 任务 7：calculator 幂运算守卫

**文件：** 改 `src/harness/tools/builtins/calculator.py`、测试 `tests/test_calculator.py`

- [ ] **步骤 1：写失败测试**（`tests/test_calculator.py` 追加）

```python
def test_pow_magnitude_guarded():
    import pytest
    from harness.tools.builtins.calculator import safe_eval
    with pytest.raises(ValueError):
        safe_eval("9 ** 99999999")
    # 正常小幂不受影响
    assert safe_eval("2 ** 10") == 1024
```

运行：预期 FAIL（当前会尝试真的计算超大幂）。

- [ ] **步骤 2：改 `src/harness/tools/builtins/calculator.py`**：加常量与幂运算守卫。在 `_OPS` 定义后、`_eval` 之前加：

```python
_MAX_POW_EXPONENT = 1000  # 防 9**99999999 类 DoS
```

把 `_eval` 中处理 `ast.BinOp` 的分支改为对幂运算特判：
```python
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left = _eval(node.left)
        right = _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_POW_EXPONENT:
            raise ValueError(f"幂运算指数过大（|{right}| > {_MAX_POW_EXPONENT}）")
        return _OPS[type(node.op)](left, right)
```

（保留 `ast.UnaryOp` 分支与 `ast.Constant` 的 `type(node.value) in (int, float)` 不变。）

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/test_calculator.py -v`　预期：全 pass。
```bash
git add src/harness/tools/builtins/calculator.py tests/test_calculator.py
git commit -m "feat: calculator 幂运算量级守卫"
```

---

## 任务 8：openai_compat 真实 usage

**文件：** 改 `src/harness/llm/openai_compat.py`、测试 `tests/test_openai_compat.py`

- [ ] **步骤 1：写失败测试**（`tests/test_openai_compat.py` 追加两条）

```python
class _FakeUsage:
    def __init__(self, p, c, t):
        self.prompt_tokens = p
        self.completion_tokens = c
        self.total_tokens = t


class _FakeEventUsage:
    """带 usage、无 choices 的尾 chunk（include_usage 行为）。"""
    def __init__(self, usage):
        self.choices = []
        self.usage = usage


async def test_done_chunk_uses_real_usage(monkeypatch):
    cfg = HarnessConfig(api_key="k")
    client = OpenAICompatibleClient(cfg)
    events = [
        _FakeEvent(_FakeDelta(content="你好")),
        _FakeEventUsage(_FakeUsage(11, 7, 18)),
    ]

    async def fake_create(**kwargs):
        assert kwargs["stream_options"] == {"include_usage": True}
        return _fake_stream(events)

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)
    out = [c async for c in client.stream([Message(role=Role.USER, content="hi")], [])]
    done = [c for c in out if c.type == "done"][0]
    assert (done.usage.prompt_tokens, done.usage.completion_tokens, done.usage.total_tokens) == (11, 7, 18)


async def test_done_chunk_falls_back_to_tiktoken(monkeypatch):
    cfg = HarnessConfig(api_key="k")
    client = OpenAICompatibleClient(cfg)
    events = [_FakeEvent(_FakeDelta(content="hello world"))]  # 无 usage 尾 chunk

    async def fake_create(**kwargs):
        return _fake_stream(events)

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)
    out = [c async for c in client.stream([Message(role=Role.USER, content="hi")], [])]
    done = [c for c in out if c.type == "done"][0]
    assert done.usage is not None
    assert done.usage.total_tokens > 0   # tiktoken 估算
```

注：`_FakeEvent`/`_FakeDelta`/`_fake_stream` 已在①的该测试文件中定义，直接复用。

运行：预期 FAIL。

- [ ] **步骤 2：改 `src/harness/llm/openai_compat.py`** 的 `stream()`：顶部 import 追加 `from ..usage import Usage, estimate_usage`，方法体改为：

```python
    async def stream(
        self, messages: list[Message], tools: list[dict]
    ) -> AsyncIterator[StreamChunk]:
        kwargs: dict = {
            "model": self._config.model,
            "messages": [m.to_openai() for m in messages],
            "temperature": self._config.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools

        completion_parts: list[str] = []
        captured = None
        stream = await self._client.chat.completions.create(**kwargs)
        async for event in stream:
            if getattr(event, "usage", None):
                captured = event.usage
            if not event.choices:
                continue
            delta = event.choices[0].delta
            if getattr(delta, "content", None):
                completion_parts.append(delta.content)
                yield StreamChunk(type="text", text=delta.content)
            for tc in (getattr(delta, "tool_calls", None) or []):
                fn = getattr(tc, "function", None)
                yield StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
                    index=tc.index,
                    id=getattr(tc, "id", None),
                    name=getattr(fn, "name", None) if fn else None,
                    arguments=getattr(fn, "arguments", None) if fn else None,
                ))

        if captured is not None:
            usage = Usage(captured.prompt_tokens, captured.completion_tokens, captured.total_tokens)
        else:
            usage = estimate_usage(messages, "".join(completion_parts), self._config.model)
        yield StreamChunk(type="done", usage=usage)
```

（注：若某兼容端点不接受 `stream_options`，可去掉该键；本项目目标端点 DeepSeek/Qwen/Kimi 均支持。）

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/test_openai_compat.py -v`　预期：全 pass。
```bash
git add src/harness/llm/openai_compat.py tests/test_openai_compat.py
git commit -m "feat: openai_compat 请求并回填真实 usage（tiktoken 兜底）"
```

---

## 任务 9：loop 集成（预算 + tracer + 自纠正 + ModelUsage）

**文件：** 改 `src/harness/loop/agent_loop.py`、测试 `tests/test_loop.py`

- [ ] **步骤 1：写失败测试**（`tests/test_loop.py` 追加；并替换①遗留的 `test_finalize_*` 用例以适配新返回类型）

先在 `_build_loop` 之外新增一个支持预算的构造器与用例：
```python
from harness.reliability.budget import BudgetTracker
from harness.events import ModelUsage
from harness.loop.agent_loop import _finalize, _accumulate
from harness.llm.base import ToolCallDelta


def test_finalize_returns_calls_and_parse_error():
    # 交错双工具 + 一个非法 JSON，验证新的 _Finalized 返回
    acc = {}
    _accumulate(acc, ToolCallDelta(index=0, id="a", name="calculator", arguments='{"expression":'))
    _accumulate(acc, ToolCallDelta(index=1, id="b", name="echo", arguments='{"text":"hi"}'))
    _accumulate(acc, ToolCallDelta(index=0, arguments='"1+1"}'))
    out = _finalize(acc)
    assert [f.call.name for f in out] == ["calculator", "echo"]
    assert out[0].call.arguments == {"expression": "1+1"}
    assert out[0].parse_error is None


def test_finalize_flags_invalid_json():
    acc = {}
    _accumulate(acc, ToolCallDelta(index=0, id="a", name="echo", arguments="not-json"))
    out = _finalize(acc)
    assert out[0].parse_error is not None
    assert out[0].call.arguments == {}


async def test_invalid_json_tool_args_self_correct(make_mock, text_turn):
    # 第一轮吐非法 JSON 工具参数 → loop 应回填 is_error 错误消息，不崩溃；第二轮作答
    from harness.llm.base import StreamChunk
    bad_tool_turn = [
        StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
            index=0, id="c1", name="calculator", arguments="not-json")),
        StreamChunk(type="done"),
    ]
    loop = _build_loop(make_mock([bad_tool_turn, text_turn("抱歉，我重发")]))
    from harness.events import ToolFinished, RunFinished
    events = [e async for e in loop.run("算点啥")]
    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert finished[0].result.is_error is True
    assert "JSON" in finished[0].result.content
    assert isinstance(events[-1], RunFinished)


async def test_token_budget_breach_emits_run_error(make_mock):
    # 预算 50，每轮请求工具且 usage=40：step1 后累计 40，step2 后 80，step3 步边界拦截
    from harness.events import RunError
    from harness.usage import Usage
    from harness.llm.base import StreamChunk

    def tool_usage_turn(i):
        return [
            StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
                index=0, id=f"c{i}", name="calculator", arguments='{"expression":"1+1"}')),
            StreamChunk(type="done", usage=Usage(20, 20, 40)),
        ]

    loop = _build_loop_with_budget(make_mock([tool_usage_turn(i) for i in range(5)]),
                                   BudgetTracker(max_tokens=50))
    events = [e async for e in loop.run("go")]
    assert isinstance(events[-1], RunError)
    assert "token" in events[-1].error


async def test_model_usage_event_emitted(make_mock, text_turn_usage):
    from harness.events import ModelUsage
    loop = _build_loop(make_mock([text_turn_usage("你好", prompt=10, completion=5)]))
    events = [e async for e in loop.run("hi")]
    mu = [e for e in events if isinstance(e, ModelUsage)]
    assert len(mu) == 1
    assert mu[0].usage.total_tokens == 15
```

并在测试文件顶部补一个带预算的构造器：
```python
def _build_loop_with_budget(client, budget, max_steps=10):
    reg = ToolRegistry()
    reg.register(CalculatorTool())
    ctx = ContextManager(system_prompt="s")
    return AgentLoop(client=client, registry=reg, context=ctx, max_steps=max_steps,
                     run_id_factory=lambda: "run-test", budget=budget)
```

> 若①实现阶段遗留的 `test_finalize_handles_interleaved_multi_tool_deltas` 仍在文件中，将其删除（已被上面 `test_finalize_returns_calls_and_parse_error` 覆盖并适配新返回类型）。

运行：预期 FAIL。

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

    async def run(self, user_message: str) -> AsyncIterator[Event]:
        state = RunState(run_id=self._new_run_id())
        state.append(Message(role=Role.USER, content=user_message))
        if self._budget:
            self._budget.start()
        yield RunStarted(run_id=state.run_id)

        with self._tracer.start_as_current_span("run") as run_span:
            run_span.set_attribute("harness.run_id", state.run_id)

            for step in range(1, self._max_steps + 1):
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
                        return

                    yield ToolCallRequested(tool_calls=tool_calls)
                    for f in finalized:
                        tc = f.call
                        yield ToolStarted(tool_call=tc)
                        if f.parse_error:  # 自纠正：回填明确错误，让模型下一步重发
                            result = ToolResult(
                                tc.id,
                                f"工具调用参数不是合法 JSON：{f.parse_error}，请重新调用。",
                                is_error=True,
                            )
                        else:
                            with self._tracer.start_as_current_span(f"tool_call:{tc.name}") as ts:
                                result = await self._executor.execute(tc)
                                ts.set_attribute("harness.tool.is_error", result.is_error)
                        state.append(Message(role=Role.TOOL, content=result.content, tool_call_id=tc.id))
                        yield ToolFinished(result=result)
                    yield StepFinished(step=step)

            yield RunError(error=f"达到 max_steps 上限 ({self._max_steps})")
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/test_loop.py -v`　预期全 pass；再 `uv run pytest -q` 确认全项目无回归。
```bash
git add src/harness/loop/agent_loop.py tests/test_loop.py
git commit -m "feat: loop 集成预算/tracer/自纠正/ModelUsage"
```

---

## 任务 10：可观测性集成测试 + 组装接线 + demo 更新

**文件：** 测试 `tests/test_telemetry_integration.py`；改 `examples/demo.py`

- [ ] **步骤 1：写 OTel span 集成测试（用 InMemorySpanExporter，不打网络）**

```python
# tests/test_telemetry_integration.py
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from harness.loop.agent_loop import AgentLoop
from harness.context.manager import ContextManager
from harness.tools.base import ToolRegistry
from harness.tools.builtins.calculator import CalculatorTool
from harness.llm.base import StreamChunk, ToolCallDelta
from harness.usage import Usage


def _tracer_and_exporter():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


async def test_run_produces_span_tree(make_mock):
    tracer, exporter = _tracer_and_exporter()
    tool_turn = [
        StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
            index=0, id="c1", name="calculator", arguments='{"expression":"1+1"}')),
        StreamChunk(type="done", usage=Usage(10, 5, 15), attempts=1),
    ]
    text_turn = [StreamChunk(type="text", text="答案 2"), StreamChunk(type="done", usage=Usage(3, 2, 5))]
    reg = ToolRegistry()
    reg.register(CalculatorTool())
    loop = AgentLoop(client=make_mock([tool_turn, text_turn]), registry=reg,
                     context=ContextManager(system_prompt="s"), max_steps=5,
                     run_id_factory=lambda: "r1", tracer=tracer)
    _ = [e async for e in loop.run("算 1+1")]

    names = [s.name for s in exporter.get_finished_spans()]
    assert "run" in names
    assert "step" in names
    assert "model_call" in names
    assert "tool_call:calculator" in names
```

运行：`uv run pytest tests/test_telemetry_integration.py -v`　预期：1 passed。

- [ ] **步骤 2：更新 `examples/demo.py`** 接入可靠性层（组装接线）。在构造 loop 处替换为：

```python
    from harness.reliability.retry import RetryingModelClient
    from harness.reliability.budget import BudgetTracker
    from harness.telemetry.tracer import setup_telemetry, get_tracer
    from harness.events import ModelUsage

    setup_telemetry(cfg)  # otel_enabled=False 时 no-op
    base_client = OpenAICompatibleClient(cfg)
    client = RetryingModelClient(base_client, max_retries=cfg.max_retries,
                                 base_delay=cfg.retry_base_delay)
    budget = BudgetTracker(max_tokens=cfg.max_tokens_budget,
                           max_wall_seconds=cfg.max_wall_seconds)
    loop = AgentLoop(
        client=client,
        registry=registry,
        context=ContextManager(system_prompt=cfg.system_prompt),
        max_steps=cfg.max_steps,
        budget=budget,
        tracer=get_tracer(),
        model_name=cfg.model,
        price_map=cfg.price_map,
        tool_result_max_chars=cfg.tool_result_max_chars,
    )
```

并在事件打印分支加一条（放在 `RunError` 分支前）：
```python
        elif isinstance(ev, ModelUsage):
            cost = f"${ev.cost_usd:.4f}" if ev.cost_usd is not None else "n/a"
            print(f"\n[用量] tokens={ev.usage.total_tokens} cost={cost} "
                  f"retries={ev.attempts - 1} latency={ev.latency_ms:.0f}ms")
```

- [ ] **步骤 3：全量测试并 commit**

运行：`uv run pytest -q`　预期：全绿（集成测试仍 1 skipped）。
```bash
git add tests/test_telemetry_integration.py examples/demo.py
git commit -m "feat: OTel span 集成测试 + demo 接入可靠性层"
```

---

## 完成标准（对照规格验收）

- [ ] 瞬时失败自动重试、耗尽 `RunError`、流中途断裂不重试（任务 4）
- [ ] 非法 JSON 工具参数回填 `is_error` 自纠正、不崩溃（任务 9）
- [ ] token 超 `max_tokens_budget` → 步边界 `RunError`（任务 9）
- [ ] 墙钟超 `max_wall_seconds` → `RunError`（任务 3 单测 + loop 路径）
- [ ] 完整 run 产生 run/step/model_call/tool_call span 树（任务 10，InMemorySpanExporter）
- [ ] 工具结果截断（任务 6）、`calculator` 幂运算守卫（任务 7）
- [ ] `ModelUsage` 事件带 token/成本/重试/延迟（任务 9）
- [ ] ①全部原有测试无回归（`uv run pytest` 全绿）
```

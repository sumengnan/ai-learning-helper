# AI Harness 多 Agent 编排（子项目③c）实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** `dispatch(agent, task)` 工具——查具名角色花名册、用角色 prompt+受控工具子集建全新子 `AgentLoop`（独立 RunState）跑到结束、返回子 agent 最终答案。共享预算 + 深度上限。

**架构：** `orchestration/`（AgentSpec + AgentRoster + DispatchTool）。`DispatchTool` 是个 `Tool`，内部编排子 loop，复用①②全部机制，loop 零改动。

**技术栈：** 沿用①②③ · **无新依赖**。

**规格：** `docs/superpowers/specs/2026-07-08-harness-multiagent-design.md`

**提交规范：** git 身份已是 sumengnan，默认提交；**任何 commit message 不得出现 Claude/AI/Co-Authored-By 等署名**。

---

## 文件结构与职责

| 文件 | 动作 | 职责 |
|---|---|---|
| `src/harness/orchestration/spec.py` | 新增 | `AgentSpec` + `AgentRoster` |
| `src/harness/orchestration/dispatch.py` | 新增 | `DispatchTool`（建子 loop + 跑 + 返回汇总 + 深度控制） |
| `src/harness/config.py` | 改 | `max_dispatch_depth`、`sub_agent_max_steps` |

**依赖方向**：`orchestration → {loop, tools, context, events}`；`loop` 不依赖 `orchestration`（无环）。

---

## 任务 0：配置

**文件：** 改 `src/harness/config.py`、测试 `tests/test_config.py`

- [ ] **步骤 1：写失败测试**（`tests/test_config.py` 追加）

```python
def test_multiagent_defaults():
    cfg = HarnessConfig(api_key="k")
    assert cfg.max_dispatch_depth == 2
    assert cfg.sub_agent_max_steps == 10
```

运行：`uv run pytest tests/test_config.py::test_multiagent_defaults -v`　预期 FAIL。

- [ ] **步骤 2：改 `src/harness/config.py`** 末尾追加：

```python
    # 多 Agent 编排
    max_dispatch_depth: int = 2       # agent 树最大层数（防无限递归）
    sub_agent_max_steps: int = 10     # 子 agent 单次 run 步数上限
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/test_config.py -v`　预期全 pass。
```bash
git add src/harness/config.py tests/test_config.py
git commit -m "chore: 多 Agent 编排配置项"
```

---

## 任务 1：AgentSpec + AgentRoster `orchestration/spec.py`

**文件：** 创建 `src/harness/orchestration/__init__.py`、`spec.py`、测试 `tests/test_agent_spec.py`

- [ ] **步骤 1：建包**　运行：`touch src/harness/orchestration/__init__.py`

- [ ] **步骤 2：写失败测试**

```python
# tests/test_agent_spec.py
from harness.orchestration.spec import AgentSpec, AgentRoster


def test_roster_get_and_names():
    r = AgentRoster([
        AgentSpec("a", "descA", "pA", ["t1"]),
        AgentSpec("b", "descB", "pB", []),
    ])
    assert r.get("a").system_prompt == "pA"
    assert r.get("missing") is None
    assert set(r.names()) == {"a", "b"}


def test_describe_contains_roles():
    r = AgentRoster([AgentSpec("researcher", "擅长检索", "p", ["browse"])])
    d = r.describe()
    assert "researcher" in d and "擅长检索" in d
```

运行：预期 FAIL。

- [ ] **步骤 3：实现 `src/harness/orchestration/spec.py`**

```python
# src/harness/orchestration/spec.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AgentSpec:
    name: str
    description: str          # 给主 agent 看的能力说明
    system_prompt: str        # 子 agent 的 system prompt
    tool_names: list[str]     # 该角色可用的工具名（从工具池选子集）


class AgentRoster:
    def __init__(self, specs: list[AgentSpec]) -> None:
        self._specs: dict[str, AgentSpec] = {s.name: s for s in specs}

    def get(self, name: str) -> AgentSpec | None:
        return self._specs.get(name)

    def names(self) -> list[str]:
        return list(self._specs.keys())

    def describe(self) -> str:
        lines = [f"- {s.name}：{s.description}" for s in self._specs.values()]
        return "可派发的子 agent 角色：\n" + "\n".join(lines)
```

- [ ] **步骤 4：跑通并 commit**

运行：`uv run pytest tests/test_agent_spec.py -v`　预期：2 passed。
```bash
git add src/harness/orchestration/__init__.py src/harness/orchestration/spec.py tests/test_agent_spec.py
git commit -m "feat: AgentSpec + AgentRoster 角色花名册"
```

---

## 任务 2：DispatchTool `orchestration/dispatch.py`

**文件：** 创建 `src/harness/orchestration/dispatch.py`、测试 `tests/test_dispatch.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/test_dispatch.py
import pytest

from harness.orchestration.spec import AgentSpec, AgentRoster
from harness.orchestration.dispatch import DispatchTool
from harness.tools.base import ToolRegistry, ToolExecutor
from harness.tools.builtins.calculator import CalculatorTool
from harness.types import ToolCall


def _dispatch(client, depth=0, max_depth=2, tools=("calculator",)):
    roster = AgentRoster([AgentSpec("researcher", "研究员", "你是研究员", list(tools))])
    pool = {"calculator": CalculatorTool()}
    return DispatchTool(roster, pool, client, depth=depth, max_depth=max_depth, sub_max_steps=5)


async def test_dispatch_runs_subagent_and_returns_answer(make_mock, text_turn):
    tool = _dispatch(make_mock([text_turn("子结果")]))
    out = await tool.run(tool.Params(agent="researcher", task="做点研究"))
    assert out == "子结果"


async def test_unknown_agent_is_error(make_mock, text_turn):
    tool = _dispatch(make_mock([text_turn("x")]))
    reg = ToolRegistry(); reg.register(tool)
    ex = ToolExecutor(reg)
    r = await ex.execute(ToolCall(id="c1", name="dispatch",
                                  arguments={"agent": "nobody", "task": "t"}))
    assert r.is_error is True
    assert "未知角色" in r.content


def test_sub_registry_only_spec_tools(make_mock, text_turn):
    tool = _dispatch(make_mock([text_turn("x")]), tools=("calculator",))
    spec = tool._roster.get("researcher")
    reg = tool._build_sub_registry(spec)
    assert reg.get("calculator") is not None
    assert reg.get("browse") is None            # 未列的工具不在


def test_depth_limit_controls_dispatch_injection(make_mock, text_turn):
    spec = AgentSpec("researcher", "r", "p", ["calculator"])
    d0 = _dispatch(make_mock([text_turn("x")]), depth=0, max_depth=2)
    assert d0._build_sub_registry(spec).get("dispatch") is not None   # depth+1=1<2 → 含
    d1 = _dispatch(make_mock([text_turn("x")]), depth=1, max_depth=2)
    assert d1._build_sub_registry(spec).get("dispatch") is None       # depth+1=2 不<2 → 不含


async def test_dispatch_description_lists_roles(make_mock, text_turn):
    tool = _dispatch(make_mock([text_turn("x")]))
    assert "researcher" in tool.description
```

运行：预期 FAIL。

- [ ] **步骤 2：实现 `src/harness/orchestration/dispatch.py`**

```python
# src/harness/orchestration/dispatch.py
from __future__ import annotations

from pydantic import BaseModel

from ..context.manager import ContextManager
from ..events import RunError, RunFinished
from ..loop.agent_loop import AgentLoop
from ..tools.base import Tool, ToolRegistry
from .spec import AgentRoster, AgentSpec


class DispatchTool(Tool):
    name = "dispatch"

    class Params(BaseModel):
        agent: str
        task: str

    def __init__(self, roster: AgentRoster, tool_pool: dict, client, budget=None,
                 tracer=None, depth: int = 0, max_depth: int = 2,
                 sub_max_steps: int = 10, model_name: str = "", price_map=None) -> None:
        self._roster = roster
        self._tool_pool = tool_pool
        self._client = client
        self._budget = budget
        self._tracer = tracer
        self._depth = depth
        self._max_depth = max_depth
        self._sub_max_steps = sub_max_steps
        self._model_name = model_name
        self._price_map = price_map
        self.description = (
            "把一个子任务派发给专职子 agent 执行，返回其最终结果。\n"
            + roster.describe())

    def _build_sub_registry(self, spec: AgentSpec) -> ToolRegistry:
        reg = ToolRegistry()
        for tname in spec.tool_names:
            tool = self._tool_pool.get(tname)
            if tool is not None:
                reg.register(tool)
        if self._depth + 1 < self._max_depth:   # 未达深度上限才给下一层派发能力
            reg.register(DispatchTool(
                self._roster, self._tool_pool, self._client, self._budget, self._tracer,
                depth=self._depth + 1, max_depth=self._max_depth,
                sub_max_steps=self._sub_max_steps, model_name=self._model_name,
                price_map=self._price_map))
        return reg

    async def run(self, params: "DispatchTool.Params") -> str:
        spec = self._roster.get(params.agent)
        if spec is None:
            raise ValueError(f"未知角色：{params.agent}。可用：{self._roster.names()}")
        sub_loop = AgentLoop(
            client=self._client, registry=self._build_sub_registry(spec),
            context=ContextManager(spec.system_prompt),
            max_steps=self._sub_max_steps, budget=self._budget,
            tracer=self._tracer, model_name=self._model_name, price_map=self._price_map)
        final = None
        error = None
        async for ev in sub_loop.run(params.task):
            if isinstance(ev, RunFinished):
                final = ev.message.content
            elif isinstance(ev, RunError):
                error = ev.error
        if final is None:
            raise RuntimeError(f"子 agent[{params.agent}] 未产出结果：{error or '未知'}")
        return final
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/test_dispatch.py -v`　预期：5 passed。
```bash
git add src/harness/orchestration/dispatch.py tests/test_dispatch.py
git commit -m "feat: DispatchTool 子 agent 派发（内嵌 loop + 深度控制）"
```

---

## 任务 3：端到端集成（含共享预算）+ demo

**文件：** 测试 `tests/test_dispatch_integration.py`、新增 `examples/multiagent_demo.py`

- [ ] **步骤 1：写集成测试**（主 loop + dispatch，单个共享 MockModelClient 脚本"主派发→子作答→主汇总"，验证共享预算累计）

```python
# tests/test_dispatch_integration.py
from harness.loop.agent_loop import AgentLoop
from harness.context.manager import ContextManager
from harness.tools.base import ToolRegistry
from harness.orchestration.spec import AgentSpec, AgentRoster
from harness.orchestration.dispatch import DispatchTool
from harness.tools.builtins.calculator import CalculatorTool
from harness.llm.base import StreamChunk, ToolCallDelta
from harness.events import ToolFinished, RunFinished
from harness.reliability.budget import BudgetTracker
from harness.usage import Usage


async def test_main_dispatches_and_summarizes(make_mock):
    dispatch_turn = [
        StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
            index=0, id="c1", name="dispatch",
            arguments='{"agent": "researcher", "task": "算 6*7"}')),
        StreamChunk(type="done", usage=Usage(10, 5, 15)),
    ]
    sub_turn = [StreamChunk(type="text", text="42"),
                StreamChunk(type="done", usage=Usage(8, 2, 10))]
    final_turn = [StreamChunk(type="text", text="研究员算出 42"),
                  StreamChunk(type="done", usage=Usage(4, 3, 7))]
    client = make_mock([dispatch_turn, sub_turn, final_turn])

    roster = AgentRoster([AgentSpec("researcher", "研究员", "你是研究员", ["calculator"])])
    pool = {"calculator": CalculatorTool()}
    budget = BudgetTracker()
    dispatch = DispatchTool(roster, pool, client=client, budget=budget,
                            depth=0, max_depth=2, sub_max_steps=5)
    reg = ToolRegistry(); reg.register(dispatch)
    loop = AgentLoop(client=client, registry=reg,
                     context=ContextManager(system_prompt="主"), max_steps=5,
                     run_id_factory=lambda: "r1", budget=budget)

    events = [e async for e in loop.run("研究 6*7")]

    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert finished[0].result.content == "42"            # 子 agent 独立上下文跑出的结果
    assert finished[0].result.is_error is False
    assert isinstance(events[-1], RunFinished)
    assert "42" in events[-1].message.content            # 主 agent 汇总
    assert budget.total_tokens == 32                     # 15+10+7 全树共享累计
```

运行：`uv run pytest tests/test_dispatch_integration.py -v`　预期：1 passed。

- [ ] **步骤 2：新增 `examples/multiagent_demo.py`**（装配示例，供手动验收）

```python
# examples/multiagent_demo.py
"""多 Agent 编排手动验收：主 agent 把子任务派给专职子 agent。
需要 .env 配好聊天端点。

运行：uv run python examples/multiagent_demo.py "先算 (12+8)*3，再解释结果"
"""
from __future__ import annotations

import asyncio
import sys

from harness.config import HarnessConfig
from harness.context.manager import ContextManager
from harness.llm.openai_compat import OpenAICompatibleClient
from harness.loop.agent_loop import AgentLoop
from harness.reliability.budget import BudgetTracker
from harness.tools.base import ToolRegistry
from harness.tools.builtins.calculator import CalculatorTool
from harness.orchestration.spec import AgentSpec, AgentRoster
from harness.orchestration.dispatch import DispatchTool
from harness.events import TextDelta, ToolStarted, ToolFinished, RunFinished, RunError


async def main(msg: str) -> None:
    cfg = HarnessConfig()
    client = OpenAICompatibleClient(cfg)
    budget = BudgetTracker(max_tokens=cfg.max_tokens_budget, max_wall_seconds=cfg.max_wall_seconds)

    pool = {"calculator": CalculatorTool()}
    roster = AgentRoster([
        AgentSpec("calculator_agent", "擅长用 calculator 精确计算",
                  "你是计算专家，用 calculator 工具算出结果并简述。", ["calculator"]),
    ])
    dispatch = DispatchTool(roster, pool, client, budget=budget,
                            depth=0, max_depth=cfg.max_dispatch_depth,
                            sub_max_steps=cfg.sub_agent_max_steps,
                            model_name=cfg.model, price_map=cfg.price_map)
    reg = ToolRegistry(); reg.register(dispatch)
    loop = AgentLoop(
        client=client, registry=reg,
        context=ContextManager(system_prompt="你是主控 agent，需要精确计算时用 dispatch 派给 calculator_agent，最后汇总回答。"),
        max_steps=cfg.max_steps, budget=budget, model_name=cfg.model)

    async for ev in loop.run(msg):
        if isinstance(ev, TextDelta):
            print(ev.text, end="", flush=True)
        elif isinstance(ev, ToolStarted):
            print(f"\n[派发/工具] {ev.tool_call.name} {ev.tool_call.arguments}")
        elif isinstance(ev, ToolFinished):
            print(f"[返回] {ev.result.content[:200]}")
        elif isinstance(ev, RunFinished):
            print(f"\n\n[完成] {ev.message.content}")
        elif isinstance(ev, RunError):
            print(f"\n\n[出错] {ev.error}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "算 (12+8)*3 并解释"))
```

- [ ] **步骤 3：全量测试并 commit**

运行：`uv run pytest -q`　预期：全绿（既有 3 个 skip 不变）。
```bash
git add tests/test_dispatch_integration.py examples/multiagent_demo.py
git commit -m "feat: 多 Agent 端到端集成测试 + demo"
```

---

## 完成标准（对照规格验收）

- [ ] `dispatch(agent, task)` 跑子 loop、返回子 agent 最终答案（任务 2/3）
- [ ] 未知角色 → `is_error`（任务 2）
- [ ] 子 registry 只含 spec 列的工具（任务 2）
- [ ] 深度上限：达 `max_depth` 的子 agent 无 `dispatch`（任务 2）
- [ ] 共享 `BudgetTracker` 累计主+子消耗（任务 3）
- [ ] "主派发→独立子上下文→返回汇总"闭环（任务 3 集成）
- [ ] ①②③a③b 无回归、无新依赖（`uv run pytest` 全绿）
```

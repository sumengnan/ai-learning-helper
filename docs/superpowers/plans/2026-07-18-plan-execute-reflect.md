# Plan-Execute-Reflect 多 Agent 编排器 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 用确定性的 Plan→Execute→Reflect 控制器替换现有 ReAct 主流程：Planner 出 DAG 计划、Scheduler 按依赖并行调度、通用 Executor（包现有 AgentLoop）执行每步、Critic 分层反思（单步校验 + 终局把关 + 有界重规划），最终 synthesize 流式产出答复；简单问答短路回退单 AgentLoop。

**架构：** 编排器子系统落 `app/orchestration/`（app 层组合逻辑，与 `completion.py`/`verify.py` 同层），因 Planner/Critic 依赖 app 层的 `call_json` 结构化输出设施——放 harness 会造成反向依赖。harness 保持纯净，`AgentLoop` 降级为 Executor 引擎与短路引擎。控制器只发**现有** Event 类型，`event_to_dict`/SSE/前端零改动。

**技术栈：** Python 3.14、asyncio、pydantic（结构化 schema 校验）、pytest（`tests/conftest.py` 的 `MockModelClient` 脚本化 LLM）。复用 `app/verify.py::call_json`、`app/completion.py::build_completer`、`harness/loop/agent_loop.py::AgentLoop`、`harness/progress.py`。

**规格：** `docs/superpowers/specs/2026-07-18-plan-execute-reflect-design.md`

---

## 文件结构

**新增（app 层）：**
- `app/orchestration/__init__.py` —— 包初始化
- `app/orchestration/plan.py` —— 数据结构（Artifact/PlanStep/Plan/Verdict/Review）+ 纯 DAG 函数（`validate_plan`/`ready_steps`/`has_pending`）
- `app/orchestration/planner.py` —— `Planner`（`plan`/`replan`，call_json + schema 校验 + 有界重试）
- `app/orchestration/executor.py` —— `Executor`（包 `AgentLoop`，把内部事件转 Progress，产出 `Artifact`）+ 内部信号 `StepArtifact`
- `app/orchestration/critic.py` —— `Critic`（`validate`/`review`，fail-open）
- `app/orchestration/orchestrator.py` —— `Orchestrator`（triage 短路 / 调度循环 / 反思重规划 / synthesize）

**测试：**
- `tests/test_orchestration_plan.py` —— 纯 DAG 函数单测
- `tests/test_orchestration_planner.py` —— Planner mock 测试
- `tests/test_orchestration_critic.py` —— Critic mock + fail-open 测试
- `tests/test_orchestration_executor.py` —— Executor mock 测试
- `tests/test_orchestration_orchestrator.py` —— 控制流端到端 mock 测试
- `tests/test_integration_real.py` —— 追加一个真实端点编排测试（无 key 时 skip）

**改动：**
- `app/config.py` —— 新增编排器 config 项
- `app/assembly.py` —— 组装 `Orchestrator` 并挂到 `Harness`
- `app/api/chat.py` —— 主路径改调 `orchestrator.run()`
- `examples/orchestrator_demo.py` —— 手动验收 demo（新增）

**保留不动：** `harness/loop/agent_loop.py`、`harness/events.py`、`harness/persistence/serialize.py`、前端、`harness/orchestration/dispatch.py`。

---

## 任务 1：数据结构与纯 DAG 函数

**文件：**
- 创建：`app/orchestration/__init__.py`
- 创建：`app/orchestration/plan.py`
- 测试：`tests/test_orchestration_plan.py`

- [ ] **步骤 1：创建包初始化文件**

创建 `app/orchestration/__init__.py`，内容为空（仅标记为包）。

```python
```

- [ ] **步骤 2：编写失败的测试**

创建 `tests/test_orchestration_plan.py`：

```python
from app.orchestration.plan import (
    Artifact, PlanStep, Plan, validate_plan, ready_steps, has_pending,
)


def _step(id, deps=(), status="pending"):
    return PlanStep(id=id, description=f"做{id}", expected=f"产出{id}",
                    depends_on=list(deps), status=status)


def test_validate_plan_ok():
    steps = [_step("s1"), _step("s2", deps=["s1"])]
    assert validate_plan(steps) is None


def test_validate_plan_duplicate_id():
    steps = [_step("s1"), _step("s1")]
    assert "重复" in validate_plan(steps)


def test_validate_plan_dangling_dep():
    steps = [_step("s1", deps=["sX"])]
    assert "sX" in validate_plan(steps)


def test_validate_plan_cycle():
    steps = [_step("s1", deps=["s2"]), _step("s2", deps=["s1"])]
    assert "环" in validate_plan(steps)


def test_validate_plan_empty():
    assert "空" in validate_plan([])


def test_ready_steps_only_deps_satisfied():
    steps = [_step("s1", status="done"), _step("s2", deps=["s1"]), _step("s3", deps=["s1", "s2"])]
    plan = Plan(goal="g", steps=steps)
    ready = ready_steps(plan)
    assert [s.id for s in ready] == ["s2"]   # s3 还差 s2


def test_ready_steps_parallel_batch():
    steps = [_step("s1"), _step("s2"), _step("s3", deps=["s1"])]
    plan = Plan(goal="g", steps=steps)
    ready = ready_steps(plan)
    assert {s.id for s in ready} == {"s1", "s2"}   # 无依赖两步同批


def test_ready_steps_blocked_by_failed():
    steps = [_step("s1", status="failed"), _step("s2", deps=["s1"])]
    plan = Plan(goal="g", steps=steps)
    assert ready_steps(plan) == []   # 依赖失败 → 永不就绪


def test_has_pending():
    assert has_pending(Plan(goal="g", steps=[_step("s1")]))
    assert not has_pending(Plan(goal="g", steps=[_step("s1", status="done")]))


def test_artifact_defaults():
    a = Artifact(summary="x")
    assert a.data == {} and a.files == []
```

- [ ] **步骤 3：运行测试验证失败**

运行：`uv run pytest tests/test_orchestration_plan.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'app.orchestration.plan'`

- [ ] **步骤 4：编写实现**

创建 `app/orchestration/plan.py`：

```python
# app/orchestration/plan.py
"""编排器数据结构与纯 DAG 函数。零 LLM 调用、零 IO，可完全确定性单测。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

StepStatus = Literal["pending", "running", "done", "failed", "skipped"]
_TERMINAL_OK = ("done",)


@dataclass
class Artifact:
    """结构化步骤产出。summary 必填（喂下游/汇总）；data/files 为结构化扩展位。"""
    summary: str
    data: dict = field(default_factory=dict)
    files: list[str] = field(default_factory=list)


@dataclass
class PlanStep:
    id: str
    description: str
    expected: str
    depends_on: list[str] = field(default_factory=list)
    status: StepStatus = "pending"
    result: Artifact | None = None
    attempts: int = 0


@dataclass
class Plan:
    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    version: int = 1


@dataclass
class Verdict:
    ok: bool
    reason: str


@dataclass
class Review:
    accept: bool
    feedback: str


def validate_plan(steps: list[PlanStep]) -> str | None:
    """校验 DAG 合法性。返回人读错误串；合法返回 None。

    三条硬约束（守住则 Scheduler 永不死锁于非法结构）：id 唯一、依赖存在、无环。
    """
    if not steps:
        return "计划为空，至少需要一个步骤"
    ids = [s.id for s in steps]
    if len(set(ids)) != len(ids):
        dup = [i for i in ids if ids.count(i) > 1]
        return f"步骤 id 重复：{sorted(set(dup))}"
    idset = set(ids)
    for s in steps:
        for d in s.depends_on:
            if d not in idset:
                return f"步骤 {s.id} 依赖不存在的步骤 {d}"
    # 拓扑排序检测环（Kahn）
    indeg = {s.id: len(s.depends_on) for s in steps}
    adj: dict[str, list[str]] = {s.id: [] for s in steps}
    for s in steps:
        for d in s.depends_on:
            adj[d].append(s.id)
    queue = [i for i, deg in indeg.items() if deg == 0]
    seen = 0
    while queue:
        cur = queue.pop()
        seen += 1
        for nxt in adj[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    if seen != len(steps):
        return "计划存在环（依赖成环），无法拓扑排序"
    return None


def ready_steps(plan: Plan) -> list[PlanStep]:
    """就绪集：自身 pending 且所有依赖已 done 的步骤。保持原始顺序。"""
    done = {s.id for s in plan.steps if s.status in _TERMINAL_OK}
    return [s for s in plan.steps
            if s.status == "pending" and all(d in done for d in s.depends_on)]


def has_pending(plan: Plan) -> bool:
    """是否仍有待处理步骤（pending 或 running）。"""
    return any(s.status in ("pending", "running") for s in plan.steps)
```

- [ ] **步骤 5：运行测试验证通过**

运行：`uv run pytest tests/test_orchestration_plan.py -q`
预期：PASS（11 passed）

- [ ] **步骤 6：Commit**

```bash
git add app/orchestration/__init__.py app/orchestration/plan.py tests/test_orchestration_plan.py
git commit -m "feat(orchestration): 数据结构与纯 DAG 函数（校验/就绪集）"
```

---

## 任务 2：Planner（出 DAG 计划 + 有界重试）

**文件：**
- 创建：`app/orchestration/planner.py`
- 测试：`tests/test_orchestration_planner.py`

依赖既有设施：`app/verify.py::call_json(complete, system, user) -> dict`（强制 JSON 输出并解析，失败抛异常）。

- [ ] **步骤 1：编写失败的测试**

创建 `tests/test_orchestration_planner.py`。用一个假 `complete`（返回 JSON 文本）驱动，无需网络：

```python
import json
import pytest

from app.orchestration.planner import Planner, PlannerError
from app.orchestration.plan import Plan


def _complete_returning(*payloads):
    """返回一个 async complete，依次吐出预设 JSON 文本（每次调用消费一个）。"""
    seq = list(payloads)
    calls = {"n": 0}

    async def complete(system, user):
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return seq[i]

    return complete, calls


async def test_plan_ok():
    payload = json.dumps({"steps": [
        {"id": "s1", "description": "查资料", "expected": "找到要点", "depends_on": []},
        {"id": "s2", "description": "汇总", "expected": "一段总结", "depends_on": ["s1"]},
    ]})
    complete, _ = _complete_returning(payload)
    plan = await Planner(complete).plan("写一篇总结")
    assert isinstance(plan, Plan)
    assert [s.id for s in plan.steps] == ["s1", "s2"]
    assert plan.steps[1].depends_on == ["s1"]
    assert plan.goal == "写一篇总结"


async def test_plan_retries_on_invalid_then_succeeds():
    bad = json.dumps({"steps": [{"id": "s1", "description": "a", "expected": "b", "depends_on": ["sX"]}]})
    good = json.dumps({"steps": [{"id": "s1", "description": "a", "expected": "b", "depends_on": []}]})
    complete, calls = _complete_returning(bad, good)
    plan = await Planner(complete, max_retries=2).plan("g")
    assert [s.id for s in plan.steps] == ["s1"]
    assert calls["n"] == 2   # 第一次非法、第二次成功


async def test_plan_raises_after_exhausting_retries():
    bad = json.dumps({"steps": [{"id": "s1", "description": "a", "expected": "b", "depends_on": ["sX"]}]})
    complete, calls = _complete_returning(bad)
    with pytest.raises(PlannerError):
        await Planner(complete, max_retries=1).plan("g")
    assert calls["n"] == 2   # 首次 + 1 次重试


async def test_replan_keeps_version_and_goal():
    p1 = json.dumps({"steps": [{"id": "s1", "description": "a", "expected": "b", "depends_on": []}]})
    complete, _ = _complete_returning(p1)
    plan = await Planner(complete).plan("g")
    p2 = json.dumps({"steps": [{"id": "s2", "description": "c", "expected": "d", "depends_on": []}]})
    complete2, _ = _complete_returning(p2)
    plan2 = await Planner(complete2).replan("g", plan, "上一版漏了X")
    assert plan2.version == plan.version + 1
    assert plan2.goal == "g"
```

注：`tests/conftest.py` 中 pytest-asyncio 已配置为自动识别 async 测试（现有 async 测试无需 `@pytest.mark.asyncio`；若运行报 "async def not natively supported"，在文件顶部加 `pytestmark = pytest.mark.asyncio`）。

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_orchestration_planner.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'app.orchestration.planner'`

- [ ] **步骤 3：编写实现**

创建 `app/orchestration/planner.py`：

```python
# app/orchestration/planner.py
"""Planner：把用户目标拆成 DAG 计划。结构化 JSON 输出 + 落地即校验 + 有界重试。"""
from __future__ import annotations

from pydantic import BaseModel, ValidationError

from app.verify import call_json

from .plan import Plan, PlanStep, validate_plan


class PlannerError(RuntimeError):
    """规划最终失败（重试耗尽或输出无法解析）。调用方据此降级为单 AgentLoop 直答。"""


class _PlanStepOut(BaseModel):
    id: str
    description: str
    expected: str
    depends_on: list[str] = []


class _PlannerOutput(BaseModel):
    steps: list[_PlanStepOut]


PLANNER_SYSTEM = (
    "你是任务规划器。把用户目标拆成 3-6 个高层子任务，输出一个有向无环图（DAG）。\n"
    "每个子任务含：id（如 s1，全局唯一）、description（要做什么）、expected（应产出什么，"
    "供质检比对）、depends_on（依赖的子任务 id 列表，无依赖填 []）。\n"
    "能并行的子任务不要人为串联（depends_on 留空）；只有真正需要前一步产出时才建立依赖。\n"
    '只输出一个 JSON 对象：{"steps":[{"id":...,"description":...,"expected":...,"depends_on":[...]}]}，'
    "不要多余文字。"
)


def _plan_user(goal: str) -> str:
    return f"用户目标：\n{goal}\n\n请拆成 DAG 计划。"


def _replan_user(goal: str, done: list[PlanStep], feedback: str) -> str:
    done_txt = "\n".join(f"- [{s.id}] {s.description}（已完成）" for s in done) or "（无）"
    return (f"用户目标：\n{goal}\n\n已完成的步骤：\n{done_txt}\n\n"
            f"质检反馈（上一版计划的不足）：\n{feedback}\n\n"
            "请只为尚未完成的部分重新规划，输出新的 DAG 计划（不要重复已完成步骤）。")


def _parse_steps(raw: dict) -> list[PlanStep]:
    """把 call_json 的 dict 校验成 PlanStep 列表。schema 不符抛 ValueError。"""
    try:
        parsed = _PlannerOutput.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"规划输出不符合 schema：{e}") from e
    return [PlanStep(id=s.id, description=s.description, expected=s.expected,
                     depends_on=list(s.depends_on)) for s in parsed.steps]


class Planner:
    def __init__(self, complete, *, max_retries: int = 2) -> None:
        self._complete = complete
        self._max_retries = max_retries

    async def plan(self, goal: str) -> Plan:
        steps = await self._generate(PLANNER_SYSTEM, _plan_user(goal))
        return Plan(goal=goal, steps=steps, version=1)

    async def replan(self, goal: str, plan: Plan, feedback: str) -> Plan:
        done = [s for s in plan.steps if s.status == "done"]
        steps = await self._generate(PLANNER_SYSTEM, _replan_user(goal, done, feedback))
        return Plan(goal=goal, steps=steps, version=plan.version + 1)

    async def _generate(self, system: str, user: str) -> list[PlanStep]:
        last_err = ""
        for attempt in range(self._max_retries + 1):
            u = user if not last_err else f"{user}\n\n上次输出无效：{last_err}。请修正后重新输出。"
            try:
                raw = await call_json(self._complete, system, u)
                steps = _parse_steps(raw)
            except Exception as e:  # 解析/schema/网络任一失败 → 记错重试
                last_err = str(e)[:200]
                continue
            err = validate_plan(steps)
            if err is None:
                return steps
            last_err = err
        raise PlannerError(f"规划失败（重试 {self._max_retries} 次后）：{last_err}")
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_orchestration_planner.py -q`
预期：PASS（4 passed）

- [ ] **步骤 5：Commit**

```bash
git add app/orchestration/planner.py tests/test_orchestration_planner.py
git commit -m "feat(orchestration): Planner 出 DAG 计划、schema 校验与有界重试"
```

---

## 任务 3：Critic（单步校验 + 终局把关，fail-open）

**文件：**
- 创建：`app/orchestration/critic.py`
- 测试：`tests/test_orchestration_critic.py`

- [ ] **步骤 1：编写失败的测试**

创建 `tests/test_orchestration_critic.py`：

```python
import json

from app.orchestration.critic import Critic
from app.orchestration.plan import Artifact, Plan, PlanStep


def _complete_json(payload_dict):
    async def complete(system, user):
        return json.dumps(payload_dict)
    return complete


def _raising_complete():
    async def complete(system, user):
        raise RuntimeError("端点抖动")
    return complete


def _step():
    return PlanStep(id="s1", description="查质数定义", expected="给出质数定义")


async def test_validate_ok():
    critic = Critic(_complete_json({"ok": True, "reason": "符合预期"}))
    v = await critic.validate(_step(), Artifact(summary="质数是只有1和自身两个因子的数"))
    assert v.ok is True


async def test_validate_fail():
    critic = Critic(_complete_json({"ok": False, "reason": "答非所问"}))
    v = await critic.validate(_step(), Artifact(summary="今天天气不错"))
    assert v.ok is False and "答非所问" in v.reason


async def test_validate_fail_open_on_error():
    """线上路径：判官调用抖动 → 放行（ok=True），绝不因基建抖动拦交付。"""
    critic = Critic(_raising_complete())
    v = await critic.validate(_step(), Artifact(summary="x"))
    assert v.ok is True and "放行" in v.reason


async def test_review_accept():
    critic = Critic(_complete_json({"accept": True, "feedback": ""}))
    plan = Plan(goal="g", steps=[_step()])
    r = await critic.review("g", plan, {"s1": Artifact(summary="ok")})
    assert r.accept is True


async def test_review_reject_with_feedback():
    critic = Critic(_complete_json({"accept": False, "feedback": "缺少示例"}))
    plan = Plan(goal="g", steps=[_step()])
    r = await critic.review("g", plan, {"s1": Artifact(summary="ok")})
    assert r.accept is False and "示例" in r.feedback


async def test_review_fail_open_on_error():
    critic = Critic(_raising_complete())
    plan = Plan(goal="g", steps=[_step()])
    r = await critic.review("g", plan, {"s1": Artifact(summary="ok")})
    assert r.accept is True and "放行" in r.feedback
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_orchestration_critic.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'app.orchestration.critic'`

- [ ] **步骤 3：编写实现**

创建 `app/orchestration/critic.py`：

```python
# app/orchestration/critic.py
"""Critic：分层反思。validate 单步校验、review 终局把关。

线上路径：判官调用抖动一律 fail-open（放行），对齐 app/verify.py「绝不因基建抖动拦交付」。
"""
from __future__ import annotations

from app.verify import call_json

from .plan import Artifact, Plan, Verdict, Review

VALIDATE_SYSTEM = (
    "你是单步质检员。给你一个子任务的描述、预期产出、以及实际产出。"
    "判断实际产出是否达成了预期产出。宽松务实：只要方向对、内容基本可用即算通过；"
    "只有明显答非所问、空洞、或与预期南辕北辙才判不通过。"
    '只输出 JSON：{"ok": true/false, "reason": "一句话理由"}。'
)

REVIEW_SYSTEM = (
    "你是终局质检员。给你用户目标和各步骤的产出。判断整体是否足以作为对用户的答复。"
    "若基本达成目标即通过（accept=true）；若有实质缺口（遗漏关键部分、明显错误）则不通过，"
    "并在 feedback 里说清缺什么，供重新规划参考。"
    '只输出 JSON：{"accept": true/false, "feedback": "不通过时说明缺口，通过可留空"}。'
)


def _validate_user(step, artifact: Artifact) -> str:
    return (f"子任务：{step.description}\n预期产出：{step.expected}\n\n"
            f"实际产出：\n{artifact.summary}\n\n请判定是否达成预期。")


def _review_user(goal: str, plan: Plan, artifacts: dict) -> str:
    lines = [f"用户目标：\n{goal}\n", "各步骤产出："]
    for s in plan.steps:
        art = artifacts.get(s.id)
        mark = art.summary if art else f"（未完成，状态={s.status}）"
        lines.append(f"- [{s.id}] {s.description}：{mark}")
    lines.append("\n请判定整体是否足以答复用户。")
    return "\n".join(lines)


class Critic:
    def __init__(self, complete) -> None:
        self._complete = complete

    async def validate(self, step, artifact: Artifact) -> Verdict:
        try:
            v = await call_json(self._complete, VALIDATE_SYSTEM, _validate_user(step, artifact))
            return Verdict(ok=bool(v.get("ok")), reason=str(v.get("reason", "")))
        except Exception as e:  # fail-open：抖动放行
            return Verdict(ok=True, reason=f"校验调用失败，放行：{str(e)[:120]}")

    async def review(self, goal: str, plan: Plan, artifacts: dict) -> Review:
        try:
            v = await call_json(self._complete, REVIEW_SYSTEM, _review_user(goal, plan, artifacts))
            return Review(accept=bool(v.get("accept")), feedback=str(v.get("feedback", "")))
        except Exception as e:  # fail-open：抖动放行
            return Review(accept=True, feedback=f"审查调用失败，放行：{str(e)[:120]}")
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_orchestration_critic.py -q`
预期：PASS（6 passed）

- [ ] **步骤 5：Commit**

```bash
git add app/orchestration/critic.py tests/test_orchestration_critic.py
git commit -m "feat(orchestration): Critic 单步校验与终局把关（fail-open）"
```

---

## 任务 4：Executor（包 AgentLoop，产出 Artifact）

**文件：**
- 创建：`app/orchestration/executor.py`
- 测试：`tests/test_orchestration_executor.py`

Executor 把内部 `AgentLoop` 的事件转成 `Progress`（与 `dispatch.py` 一致，避免把中间步骤的文本当成最终答复流给用户），并在结束时产出内部信号 `StepArtifact`（非 Event，Orchestrator 消费不外发）。

- [ ] **步骤 1：编写失败的测试**

创建 `tests/test_orchestration_executor.py`。用 `conftest.py` 的 `MockModelClient`（脚本化 LLM）驱动真实 `AgentLoop`：

```python
from harness.events import Progress
from harness.llm.base import StreamChunk
from harness.tools.base import ToolRegistry
from app.orchestration.executor import Executor, StepArtifact
from app.orchestration.plan import Artifact, PlanStep


def _step(deps=()):
    return PlanStep(id="s1", description="回答质数定义", expected="质数定义",
                    depends_on=list(deps))


def _text_only_turns(text):
    # 一轮：吐正文 + done（无工具）→ AgentLoop 直接 RunFinished
    return [[StreamChunk(type="text", text=text), StreamChunk(type="done")]]


async def _collect(gen):
    events, artifact = [], None
    async for ev in gen:
        if isinstance(ev, StepArtifact):
            artifact = ev.artifact
        else:
            events.append(ev)
    return events, artifact


async def test_executor_produces_artifact_from_final_text(make_mock):
    client = make_mock(_text_only_turns("质数是只有1和自身两个因子的自然数"))
    ex = Executor(client=client, registry=ToolRegistry(), system_prompt="你是执行者", model="m", max_steps=3)
    events, artifact = await _collect(ex.execute(_step(), {}))
    assert isinstance(artifact, Artifact)
    assert "质数" in artifact.summary


async def test_executor_emits_progress_not_textdelta(make_mock):
    """执行者内部产出不得作为 TextDelta 泄露给用户；只出 Progress。"""
    client = make_mock(_text_only_turns("中间产出"))
    ex = Executor(client=client, registry=ToolRegistry(), system_prompt="sp", model="m", max_steps=3)
    events, _ = await _collect(ex.execute(_step(), {}))
    from harness.events import TextDelta
    assert not any(isinstance(e, TextDelta) for e in events)
    assert all(isinstance(e, Progress) for e in events)


async def test_executor_injects_dep_artifacts(make_mock):
    """依赖产物应进入 Executor 的 prompt（用 mock 无法看 prompt，改测不崩且产出正常）。"""
    client = make_mock(_text_only_turns("已参考前置结果"))
    ex = Executor(client=client, registry=ToolRegistry(), system_prompt="sp", model="m", max_steps=3)
    deps = {"s0": Artifact(summary="前置：X=42")}
    _, artifact = await _collect(ex.execute(_step(deps=["s0"]), deps))
    assert artifact is not None
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_orchestration_executor.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'app.orchestration.executor'`

- [ ] **步骤 3：编写实现**

创建 `app/orchestration/executor.py`：

```python
# app/orchestration/executor.py
"""Executor：执行单个计划步骤。

每步起一个独立上下文的 AgentLoop（通用 prompt + 全量工具），实现上下文隔离。
内部事件转 Progress（与 dispatch 一致），最终以 StepArtifact 信号带出结构化产物。
"""
from __future__ import annotations

from dataclasses import dataclass

from harness.context.manager import ContextManager
from harness.events import Progress, RunError, RunFinished, ToolFinished, ToolStarted
from harness.loop.agent_loop import AgentLoop
from harness.progress import reset_current_agent, set_current_agent
from harness.tools.base import ToolRegistry

from .plan import Artifact, PlanStep


@dataclass
class StepArtifact:
    """内部信号：Executor 产出的最终产物。Orchestrator 消费、不外发（非 Event）。"""
    artifact: Artifact
    error: str | None = None


def _build_prompt(step: PlanStep, deps: dict[str, Artifact], hint: str = "") -> str:
    lines = [f"你的子任务：{step.description}", f"预期产出：{step.expected}"]
    if deps:
        lines.append("\n已知前置步骤的产出（供参考，不要重复其工作）：")
        for dep_id, art in deps.items():
            lines.append(f"[{dep_id}] {art.summary}")
    if hint:
        lines.append(f"\n上次尝试未通过质检，请改进：{hint}")
    lines.append("\n完成后直接给出该子任务的结果。")
    return "\n".join(lines)


class Executor:
    def __init__(self, client, registry: ToolRegistry, system_prompt: str,
                 model: str, *, max_steps: int = 10, budget=None,
                 loop_detect_window: int = 0) -> None:
        self._client = client
        self._registry = registry
        self._system_prompt = system_prompt
        self._model = model
        self._max_steps = max_steps
        self._budget = budget
        self._loop_detect_window = loop_detect_window

    async def execute(self, step: PlanStep, deps: dict[str, Artifact], hint: str = ""):
        """执行一步。yield Progress 事件，最后 yield 一个 StepArtifact。"""
        prompt = _build_prompt(step, deps, hint)
        loop = AgentLoop(
            client=self._client, registry=self._registry,
            context=ContextManager(self._system_prompt),
            max_steps=self._max_steps, budget=self._budget, model_name=self._model,
            loop_detect_window=self._loop_detect_window)
        scope = f"subagent:executor:{step.id}"
        final_text = ""
        error = None
        tool_names: dict[str, str] = {}
        token = set_current_agent(f"executor:{step.id}")
        try:
            async for ev in loop.run(prompt):
                if isinstance(ev, ToolStarted):
                    tool_names[ev.tool_call.id] = ev.tool_call.name
                    yield Progress(scope, f"调用工具 {ev.tool_call.name}",
                                   status="running", key=ev.tool_call.id)
                elif isinstance(ev, ToolFinished):
                    r = ev.result
                    name = tool_names.get(r.tool_call_id, "工具")
                    yield Progress(scope, f"调用工具 {name}",
                                   status="error" if r.is_error else "ok", key=r.tool_call_id)
                elif isinstance(ev, RunFinished):
                    final_text = ev.message.content or ""
                elif isinstance(ev, RunError):
                    error = ev.error
        finally:
            reset_current_agent(token)
        yield StepArtifact(Artifact(summary=final_text, data={}, files=[]), error=error)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_orchestration_executor.py -q`
预期：PASS（3 passed）

- [ ] **步骤 5：Commit**

```bash
git add app/orchestration/executor.py tests/test_orchestration_executor.py
git commit -m "feat(orchestration): Executor 包 AgentLoop、事件转 Progress、产出 Artifact"
```

---

## 任务 5：Orchestrator 控制器（triage / 调度 / 反思 / synthesize）

**文件：**
- 创建：`app/orchestration/orchestrator.py`
- 测试：`tests/test_orchestration_orchestrator.py`

这是核心控制器。为便于确定性测试，`Orchestrator` 的构造注入 planner、critic、executor 工厂、triage/synthesize 用的 completer——测试可传假实现。

- [ ] **步骤 1：编写失败的测试（控制流全景）**

创建 `tests/test_orchestration_orchestrator.py`。用假 Planner/Critic/Executor 驱动，断言控制流：

```python
from harness.events import Progress, RunError, RunFinished, RunStarted, TextDelta
from app.orchestration.orchestrator import Orchestrator
from app.orchestration.plan import Artifact, Plan, PlanStep, Verdict, Review


# ---- 测试替身 ----
class FakePlanner:
    def __init__(self, plans):
        self._plans = list(plans); self._i = 0
    async def plan(self, goal):
        p = self._plans[0]; return p
    async def replan(self, goal, plan, feedback):
        self._i += 1
        return self._plans[min(self._i, len(self._plans) - 1)]


class FakeCritic:
    def __init__(self, validate_ok=True, reviews=(True,)):
        self._validate_ok = validate_ok
        self._reviews = list(reviews); self._ri = 0
    async def validate(self, step, artifact):
        return Verdict(ok=self._validate_ok, reason="")
    async def review(self, goal, plan, artifacts):
        r = self._reviews[min(self._ri, len(self._reviews) - 1)]; self._ri += 1
        return Review(accept=r, feedback="补一下X")


class FakeExecutor:
    """每步产出 summary=step.id 的 Artifact；记录执行顺序供并行断言。"""
    def __init__(self, order):
        self._order = order
    async def execute(self, step, deps, hint=""):
        from app.orchestration.executor import StepArtifact
        self._order.append(step.id)
        yield Progress(f"subagent:executor:{step.id}", "开始")
        yield StepArtifact(Artifact(summary=f"done-{step.id}"))


def _mk(planner, critic, order, triage_simple=False, synth="最终答复", max_replan=2):
    async def fake_triage(msg):
        return triage_simple
    async def fake_synth(goal, artifacts):
        from harness.events import TextDelta
        yield TextDelta(text=synth)
    async def fake_simple(msg):
        yield RunFinished(message=__import__("harness.types", fromlist=["Message"]).Message(
            role=__import__("harness.types", fromlist=["Role"]).Role.ASSISTANT, content="简单答复"))
    orch = Orchestrator.__new__(Orchestrator)
    orch._planner = planner
    orch._critic = critic
    orch._executor = FakeExecutor(order)
    orch._is_simple = fake_triage
    orch._synthesize = fake_synth
    orch._simple_answer = fake_simple
    orch._max_replan = max_replan
    orch._max_step_retry = 2
    orch._budget = None
    return orch


async def _run(orch, msg="做点复杂的事"):
    return [ev async for ev in orch.run(msg)]


def _plan(*steps):
    return Plan(goal="g", steps=list(steps))


def _s(id, deps=()):
    return PlanStep(id=id, description=id, expected=id, depends_on=list(deps))


async def test_happy_path_emits_expected_event_sequence():
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2", deps=["s1"]))]),
               FakeCritic(validate_ok=True, reviews=(True,)), order)
    events = await _run(orch)
    assert isinstance(events[0], RunStarted)
    assert isinstance(events[-1], RunFinished)
    assert events[-1].message.content == "最终答复"
    assert any(isinstance(e, TextDelta) for e in events)   # synthesize 流式
    assert order == ["s1", "s2"]   # 依赖串行


async def test_parallel_steps_run_in_same_batch():
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2"))]),
               FakeCritic(reviews=(True,)), order)
    await _run(orch)
    assert set(order) == {"s1", "s2"}   # 两步无依赖，均执行


async def test_triage_short_circuit():
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(), order, triage_simple=True)
    events = await _run(orch, "你好")
    assert events[-1].message.content == "简单答复"
    assert order == []   # 未进编排


async def test_reject_then_replan_then_accept():
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1")), _plan(_s("s2"))]),
               FakeCritic(validate_ok=True, reviews=(False, True)), order)
    events = await _run(orch)
    assert isinstance(events[-1], RunFinished)
    assert "s2" in order   # 重规划后的新步骤被执行


async def test_deadlock_emits_run_error():
    order = []
    # s1 依赖 s2、s2 依赖 s1 是非法 DAG，但 FakePlanner 不校验；
    # 用「依赖一个永不就绪(不存在于就绪逻辑)的步」构造死锁：s1 依赖 sX（sX 不在计划里）
    orch = _mk(FakePlanner([_plan(_s("s1", deps=["sX"]))]), FakeCritic(), order)
    events = await _run(orch)
    assert any(isinstance(e, RunError) for e in events)
    assert not isinstance(events[-1], RunFinished)


async def test_validate_fail_retries_bounded_then_failed():
    order = []
    # validate 恒失败：s1 会重试到 max_step_retry 后置 failed；review 放行 → 带残缺定稿
    orch = _mk(FakePlanner([_plan(_s("s1"))]),
               FakeCritic(validate_ok=False, reviews=(True,)), order)
    events = await _run(orch)
    assert isinstance(events[-1], RunFinished)   # 不因单步失败崩溃
    assert order.count("s1") == 2                # 初次 + 1 次重试（max_step_retry=2）
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_orchestration_orchestrator.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'app.orchestration.orchestrator'`

- [ ] **步骤 3：编写实现**

创建 `app/orchestration/orchestrator.py`：

```python
# app/orchestration/orchestrator.py
"""Orchestrator：确定性 Plan→Execute→Reflect 控制器。

产出与 AgentLoop 完全相同的 Event 类型（RunStarted/Progress/TextDelta/RunFinished/RunError），
故 event_to_dict/SSE/前端零改动。控制流见 spec §4。
"""
from __future__ import annotations

import asyncio
import json
import uuid

from harness.context.manager import ContextManager
from harness.events import (
    Progress, RunError, RunFinished, RunStarted, TextDelta,
)
from harness.loop.agent_loop import AgentLoop
from harness.reliability.budget import BudgetExceeded
from harness.tools.base import ToolRegistry
from harness.types import Message, Role

from .critic import Critic
from .executor import Executor, StepArtifact
from .planner import Planner, PlannerError
from .plan import Artifact, Plan, has_pending, ready_steps

TRIAGE_SYSTEM = (
    "判断用户消息是否为简单问答（打招呼、寒暄、单句事实、闲聊）。"
    "多步任务、需要检索/代码/工具、需要规划的一律算复杂。"
    "只回一个词：simple 或 complex。"
)

SYNTH_SYSTEM = (
    "你是汇总员。根据用户目标和各步骤的产出，写出面向用户的最终答复。"
    "只使用已给出的产出，不要编造；条理清晰、直接作答。"
)


def _synth_user(goal: str, artifacts: dict[str, Artifact]) -> str:
    lines = [f"用户目标：\n{goal}\n", "各步骤产出："]
    for sid, art in artifacts.items():
        lines.append(f"[{sid}] {art.summary}")
    lines.append("\n请综合以上，写出对用户的最终答复。")
    return "\n".join(lines)


def _plan_progress(plan: Plan) -> Progress:
    """把 Plan 转成前端 plan UI 认的 [{title,status}] 形状，走 Progress(scope=plan)。"""
    steps = [{"title": s.description, "status": s.status} for s in plan.steps]
    return Progress(scope="plan", text=json.dumps(steps, ensure_ascii=False), key="plan")


class Orchestrator:
    def __init__(self, *, client, registry: ToolRegistry, model: str,
                 planner: Planner, critic: Critic, executor: Executor,
                 fast_complete, budget=None,
                 max_step_retry: int = 2, max_replan: int = 2) -> None:
        self._client = client
        self._registry = registry
        self._model = model
        self._planner = planner
        self._critic = critic
        self._executor = executor
        self._fast_complete = fast_complete
        self._budget = budget
        self._max_step_retry = max_step_retry
        self._max_replan = max_replan

    # ---- triage ----
    async def _is_simple(self, message: str) -> bool:
        try:
            raw = await self._fast_complete(TRIAGE_SYSTEM, message)
            return raw.strip().lower().startswith("simple")
        except Exception:
            return False   # 判不了就走完整编排（宁可多做不可少做）

    async def _simple_answer(self, message: str):
        """简单问答短路：单个全能力 AgentLoop 直答，透传其事件（跳过其 RunStarted，避免重复）。"""
        loop = AgentLoop(client=self._client, registry=self._registry,
                         context=ContextManager(SYNTH_SYSTEM),
                         max_steps=10, budget=self._budget, model_name=self._model)
        async for ev in loop.run(message):
            if isinstance(ev, RunStarted):
                continue
            yield ev

    # ---- synthesize ----
    async def _synthesize(self, goal: str, artifacts: dict[str, Artifact]):
        """流式汇总最终答复。只 yield TextDelta；run() 累加这些 delta 得最终文本。"""
        loop = AgentLoop(
            client=self._client, registry=ToolRegistry(),
            context=ContextManager(SYNTH_SYSTEM), max_steps=1, model_name=self._model)
        final = ""
        streamed = False
        async for ev in loop.run(_synth_user(goal, artifacts)):
            if isinstance(ev, TextDelta):
                streamed = True
                yield ev
            elif isinstance(ev, RunFinished):
                final = ev.message.content or ""
        # 端点未流式（只在 RunFinished 给全量）时，补一个 TextDelta，保证 run() 能累加到文本
        if final and not streamed:
            yield TextDelta(text=final)

    # ---- 主入口 ----
    async def run(self, user_message: str):
        run_id = uuid.uuid4().hex
        yield RunStarted(run_id=run_id)
        if self._budget:
            self._budget.start()

        if await self._is_simple(user_message):
            async for ev in self._simple_answer(user_message):
                yield ev
            return

        try:
            plan = await self._planner.plan(user_message)
        except PlannerError:
            async for ev in self._simple_answer(user_message):   # 降级
                yield ev
            return
        yield _plan_progress(plan)

        replan_count = 0
        retry_hints: dict[str, str] = {}
        while True:
            deadlock = False
            async for ev in self._schedule_rounds(plan, retry_hints):
                yield ev
                if isinstance(ev, RunError):
                    deadlock = True
            if deadlock:
                return

            artifacts = {s.id: s.result for s in plan.steps
                         if s.status == "done" and s.result}
            review = await self._critic.review(user_message, plan, artifacts)
            yield Progress(scope="reflect", text=("通过" if review.accept else f"需改进：{review.feedback}"))
            if review.accept or replan_count >= self._max_replan:
                break
            replan_count += 1
            for s in plan.steps:
                if s.status in ("pending", "running"):
                    s.status = "skipped"
            try:
                plan = await self._planner.replan(user_message, plan, review.feedback)
            except PlannerError:
                break
            retry_hints = {}
            yield _plan_progress(plan)

        artifacts = {s.id: s.result for s in plan.steps if s.status == "done" and s.result}
        # 累加 synthesize 吐出的 TextDelta 即最终文本——不用侧信道，真/假 _synthesize 都适用
        final_parts: list[str] = []
        async for ev in self._synthesize(user_message, artifacts):
            if isinstance(ev, TextDelta):
                final_parts.append(ev.text)
            yield ev
        final = "".join(final_parts) or "（未能生成答复）"
        yield RunFinished(message=Message(role=Role.ASSISTANT, content=final))

    # ---- 调度：一轮轮跑就绪集，直到无 pending 或死锁 ----
    async def _schedule_rounds(self, plan: Plan, retry_hints: dict[str, str]):
        while has_pending(plan):
            if self._budget:
                try:
                    self._budget.check()
                except BudgetExceeded as e:
                    yield RunError(error=e.reason)
                    return
            ready = ready_steps(plan)
            if not ready:
                yield RunError(error="计划无法推进：存在无法满足的依赖或前置步骤全部失败")
                return
            for s in ready:
                s.status = "running"
            queue: asyncio.Queue = asyncio.Queue()

            async def _worker(step):
                deps = {d: nxt.result for d in step.depends_on
                        for nxt in plan.steps if nxt.id == d and nxt.result}
                art = None
                err = None
                try:
                    async for ev in self._executor.execute(step, deps, retry_hints.get(step.id, "")):
                        if isinstance(ev, StepArtifact):
                            art, err = ev.artifact, ev.error
                        else:
                            await queue.put(("ev", ev))
                except Exception as e:  # 单步崩溃隔离
                    await queue.put(("done", (step, None, str(e))))
                    return
                await queue.put(("done", (step, art, err)))

            tasks = [asyncio.create_task(_worker(s)) for s in ready]
            remaining = len(tasks)
            while remaining:
                kind, payload = await queue.get()
                if kind == "ev":
                    yield payload
                    continue
                step, art, err = payload
                remaining -= 1
                if art is None or err:
                    self._on_step_fail(step, retry_hints, err or "执行未产出结果")
                    continue
                verdict = await self._critic.validate(step, art)
                if verdict.ok:
                    step.status = "done"
                    step.result = art
                    retry_hints.pop(step.id, None)
                else:
                    self._on_step_fail(step, retry_hints, verdict.reason)
            yield _plan_progress(plan)

    def _on_step_fail(self, step, retry_hints: dict[str, str], reason: str) -> None:
        step.attempts += 1
        if step.attempts < self._max_step_retry:
            step.status = "pending"        # 重试：回到就绪集
            retry_hints[step.id] = reason
        else:
            step.status = "failed"         # 放弃：依赖链自然断掉，交终局 Critic 判
            retry_hints.pop(step.id, None)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_orchestration_orchestrator.py -q`
预期：PASS（6 passed）

若 `test_deadlock_emits_run_error` 未通过：确认 `ready_steps` 对「依赖不存在的 id」返回空（`all(d in done ...)` 恒 False），从而 `_schedule_rounds` 走 `not ready` 分支发 `RunError`。

- [ ] **步骤 5：全量回归**

运行：`uv run pytest tests/test_orchestration_*.py -q`
预期：全部 PASS

- [ ] **步骤 6：Commit**

```bash
git add app/orchestration/orchestrator.py tests/test_orchestration_orchestrator.py
git commit -m "feat(orchestration): Orchestrator 控制器（triage/调度/反思/synthesize）"
```

---

## 任务 6：配置项

**文件：**
- 修改：`app/config.py`
- 测试：`tests/test_orchestration_config.py`

- [ ] **步骤 1：编写失败的测试**

创建 `tests/test_orchestration_config.py`：

```python
from app.config import AppConfig


def test_orchestrator_config_defaults(monkeypatch):
    monkeypatch.setenv("HARNESS_API_KEY", "")
    cfg = AppConfig()
    assert cfg.enable_orchestrator is False
    assert cfg.orchestrator_max_step_retry == 2
    assert cfg.orchestrator_max_replan == 2
    assert cfg.orchestrator_planner_max_retries == 2
    assert cfg.orchestrator_step_max_steps >= 1


def test_orchestrator_config_env_override(monkeypatch):
    monkeypatch.setenv("HARNESS_ENABLE_ORCHESTRATOR", "true")
    monkeypatch.setenv("HARNESS_ORCHESTRATOR_MAX_REPLAN", "3")
    cfg = AppConfig()
    assert cfg.enable_orchestrator is True
    assert cfg.orchestrator_max_replan == 3
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_orchestration_config.py -q`
预期：FAIL，`AttributeError: ... 'enable_orchestrator'`

- [ ] **步骤 3：编写实现**

在 `app/config.py` 的 `AppConfig` 类里（参照 `enable_dispatch` 附近的字段风格）新增：

```python
    # === Plan-Execute-Reflect 编排器 ===
    enable_orchestrator: bool = False          # 开则主流程走编排器，否则维持 ReAct AgentLoop
    orchestrator_max_step_retry: int = 2       # 单步反复失败上限（含首次）
    orchestrator_max_replan: int = 2           # 终局重规划轮数上限
    orchestrator_planner_max_retries: int = 2  # Planner 出无效 DAG 的重试上限
    orchestrator_step_max_steps: int = 10      # 每个 Executor 步内部 AgentLoop 的步数上限
```

字段依赖 `BaseSettings` 的 `HARNESS_` 前缀自动读环境变量（见 `HarnessConfig`）。

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_orchestration_config.py -q`
预期：PASS（2 passed）

- [ ] **步骤 5：Commit**

```bash
git add app/config.py tests/test_orchestration_config.py
git commit -m "feat(orchestration): 新增编排器配置项（默认关闭）"
```

---

## 任务 7：assembly 组装 Orchestrator

**文件：**
- 修改：`app/assembly.py`
- 测试：`tests/test_orchestration_assembly.py`

策略：`enable_orchestrator` 为真时，用现成的 `reg`（全量工具）、`client`、`build_fast_completer`/`build_completer` 组装 `Orchestrator`，挂到 `Harness.orchestrator`。默认关闭 → 现有装配完全不受影响（零行为变更）。

- [ ] **步骤 1：编写失败的测试**

创建 `tests/test_orchestration_assembly.py`：

```python
from app.assembly import build_harness
from app.config import AppConfig


def test_orchestrator_absent_by_default(monkeypatch):
    monkeypatch.setenv("HARNESS_API_KEY", "")
    monkeypatch.setenv("HARNESS_ENABLE_ORCHESTRATOR", "false")
    h = build_harness(AppConfig())
    assert getattr(h, "orchestrator", None) is None


def test_orchestrator_built_when_enabled(monkeypatch):
    monkeypatch.setenv("HARNESS_API_KEY", "sk-test")
    monkeypatch.setenv("HARNESS_ENABLE_ORCHESTRATOR", "true")
    h = build_harness(AppConfig())
    from app.orchestration.orchestrator import Orchestrator
    assert isinstance(h.orchestrator, Orchestrator)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_orchestration_assembly.py -q`
预期：FAIL（`orchestrator` 字段不存在 / 未组装）

- [ ] **步骤 3：编写实现**

在 `app/assembly.py`：

(a) 给 `Harness` dataclass 增加字段（在 `mcp_manager` 附近）：

```python
    orchestrator: object | None = None       # 启用编排器时的 Plan-Execute-Reflect 控制器
```

(b) 在 `build_harness` 的 `return Harness(...)` 之前、`traj = TrajectoryStore(...)` 之后，插入组装块：

```python
    orchestrator = None
    if config.enable_orchestrator:
        from harness.reliability.budget import BudgetTracker
        from app.completion import build_completer, build_fast_completer
        from app.orchestration.orchestrator import Orchestrator
        from app.orchestration.planner import Planner
        from app.orchestration.critic import Critic
        from app.orchestration.executor import Executor
        _plan_complete = build_completer(client, config.model)     # 规划/裁判用主模型（判断质量要求高）
        _fast_complete = build_fast_completer(client, config)      # triage 用快速档
        orchestrator = Orchestrator(
            client=client, registry=reg, model=config.model,
            planner=Planner(_plan_complete, max_retries=config.orchestrator_planner_max_retries),
            critic=Critic(_plan_complete),
            executor=Executor(client, reg, config.app_system_prompt, config.model,
                              max_steps=config.orchestrator_step_max_steps,
                              loop_detect_window=config.loop_detect_window),
            fast_complete=_fast_complete,
            max_step_retry=config.orchestrator_max_step_retry,
            max_replan=config.orchestrator_max_replan)
```

(c) 把 `orchestrator=orchestrator` 加进 `return Harness(...)` 的参数。

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_orchestration_assembly.py -q`
预期：PASS（2 passed）

- [ ] **步骤 5：回归既有装配测试**

运行：`uv run pytest tests/test_backend_count.py tests/test_config.py -q`
预期：PASS（确认默认关闭时零行为变更）

- [ ] **步骤 6：Commit**

```bash
git add app/assembly.py tests/test_orchestration_assembly.py
git commit -m "feat(orchestration): assembly 按开关组装 Orchestrator（默认关闭）"
```

---

## 任务 8：chat.py 接入（按开关切主流程）

**文件：**
- 修改：`app/api/chat.py`
- 测试：`tests/test_orchestration_chat_route.py`

**谨慎**：`chat.py` 的 pump 机制较厚（verify 门、plan 收尾、交付门）。本任务只做**最小接入**：`enable_orchestrator` 为真时，主流程改为消费 `harness.orchestrator.run()` 的事件并走现有 `_sse` 序列化；**不拆除**既有 ReAct 路径（开关为假时原样运行）。verify 门/`_finalize_stale_plan` 在编排器路径上天然不触发（编排器自带质量把关与计划终态），无需删除代码。

- [ ] **步骤 1：先定位接入点**

阅读 `app/api/chat.py` 中 `chat()` 内构造 `AgentLoop` 并 pump 事件到 SSE 的主段（`_new_loop` / `_drain` / `pump` 附近）。确认事件如何经 `_sse(ev)` 下发。

- [ ] **步骤 2：编写失败的测试（编排器路径产出 SSE 事件）**

创建 `tests/test_orchestration_chat_route.py`（用 FastAPI TestClient + 假 orchestrator，验证路由把编排事件转成 SSE）：

```python
import json
import pytest
from harness.events import RunStarted, TextDelta, RunFinished
from harness.types import Message, Role


class FakeOrchestrator:
    async def run(self, message):
        yield RunStarted(run_id="r1")
        yield TextDelta(text="答复")
        yield RunFinished(message=Message(role=Role.ASSISTANT, content="答复"))


@pytest.mark.skip(reason="接入点确定后填入实际路由构造；见步骤 3")
def test_orchestrator_route_streams_sse():
    ...
```

先跳过占位，步骤 3 接入后回填断言（依据 `chat.py` 实际的 router 构造与鉴权依赖，参照现有 `tests/` 中调用 `make_chat_router` 的测试写法）。

- [ ] **步骤 3：编写实现（最小分支）**

在 `chat()` 的主 pump 逻辑入口处加分支：`enable_orchestrator` 且 `harness.orchestrator` 存在时，用编排器事件源替换 `AgentLoop` 事件源，复用同一套 `_sse` 下发与轨迹落库。伪代码（按实际变量名落地）：

```python
    if config.enable_orchestrator and getattr(harness, "orchestrator", None) is not None:
        event_source = harness.orchestrator.run(user_text)
    else:
        event_source = _existing_agentloop_events(...)   # 现有路径不动
    async for ev in event_source:
        yield _sse(ev)
        # 复用现有轨迹 sink（harness.sink.wrap 或等价）落库
```

保持轨迹落库、Progress 归属、审批事件透传不变——因为编排器只发现有事件类型。

- [ ] **步骤 4：回填并运行路由测试**

按实际 router 构造回填步骤 2 的测试断言（去掉 `skip`），运行：
`uv run pytest tests/test_orchestration_chat_route.py -q`
预期：PASS

- [ ] **步骤 5：回归 chat 既有测试**

运行：`uv run pytest tests/ -k chat -q`
预期：PASS（默认开关关闭，既有行为不变）

- [ ] **步骤 6：Commit**

```bash
git add app/api/chat.py tests/test_orchestration_chat_route.py
git commit -m "feat(orchestration): chat 主流程按开关接入 Orchestrator"
```

---

## 任务 9：手动验收 demo

**文件：**
- 创建：`examples/orchestrator_demo.py`

- [ ] **步骤 1：编写 demo**

创建 `examples/orchestrator_demo.py`：

```python
# examples/orchestrator_demo.py
"""Plan-Execute-Reflect 编排器手动验收：一个多步任务走完整 规划→执行→反思→汇总。
需要 .env 配好聊天端点（HARNESS_API_KEY / HARNESS_BASE_URL / HARNESS_MODEL）。

运行：uv run python examples/orchestrator_demo.py "调研快排与归并排序的差异并给出选择建议"
"""
from __future__ import annotations

import asyncio
import sys

from app.assembly import build_harness
from app.config import AppConfig
from harness.events import Progress, RunError, RunFinished, TextDelta


async def main(msg: str) -> None:
    cfg = AppConfig(enable_orchestrator=True)
    if not cfg.api_key:
        print("需在 .env 配 HARNESS_API_KEY 才能跑真实端点 demo"); return
    h = build_harness(cfg)
    async for ev in h.orchestrator.run(msg):
        if isinstance(ev, Progress):
            if ev.scope == "plan":
                print(f"\n[计划] {ev.text}")
            elif ev.scope == "reflect":
                print(f"\n[反思] {ev.text}")
            else:
                print(f"\n[{ev.scope}] {ev.text}")
        elif isinstance(ev, TextDelta):
            print(ev.text, end="", flush=True)
        elif isinstance(ev, RunFinished):
            print(f"\n\n[完成] {ev.message.content}")
        elif isinstance(ev, RunError):
            print(f"\n\n[出错] {ev.error}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "调研快排与归并排序的差异并给出选择建议"))
```

- [ ] **步骤 2：语法/导入校验（不打网络）**

运行：`uv run python -c "import ast; ast.parse(open('examples/orchestrator_demo.py').read()); print('OK')"`
预期：`OK`

- [ ] **步骤 3：Commit**

```bash
git add examples/orchestrator_demo.py
git commit -m "docs(examples): 新增编排器手动验收 demo"
```

---

## 任务 10：真实端点集成测试

**文件：**
- 修改：`tests/test_integration_real.py`

- [ ] **步骤 1：追加编排器端到端测试**

在 `tests/test_integration_real.py` 末尾追加（复用文件顶部已有的 `pytestmark = skipif(无 API_KEY)`）：

```python
async def test_real_endpoint_orchestrator_flow():
    from app.assembly import build_harness
    from app.config import AppConfig
    from harness.events import RunFinished

    cfg = AppConfig(enable_orchestrator=True)
    h = build_harness(cfg)
    final = ""
    async for ev in h.orchestrator.run("用一段话解释什么是二分查找，并给出它的时间复杂度"):
        if isinstance(ev, RunFinished):
            final = ev.message.content or ""
    assert final                       # 有产出
    assert "二分" in final or "O(log" in final   # 命中主题
```

- [ ] **步骤 2：本地有 key 时运行；无 key 时确认 skip**

运行：`uv run pytest tests/test_integration_real.py -q`
预期：有 `HARNESS_API_KEY` → PASS；无 → skipped

- [ ] **步骤 3：Commit**

```bash
git add tests/test_integration_real.py
git commit -m "test(orchestration): 追加真实端点编排器端到端测试"
```

---

## 任务 11：全量回归与收尾

- [ ] **步骤 1：跑全量测试**

运行：`uv run pytest -q`
预期：全绿（真实端点测试在无 key 时 skip）

- [ ] **步骤 2：确认默认关闭时零行为变更**

确认 `enable_orchestrator` 默认 `False`，既有 ReAct 路径与所有既有测试不受影响。

- [ ] **步骤 3：更新文档（如有 README/配置说明）**

在 `.env.example` 追加编排器开关及默认值的注释条目（参照现有条目风格）：

```
# === Plan-Execute-Reflect 编排器（开则主流程走计划-执行-反思，默认关）===
HARNESS_ENABLE_ORCHESTRATOR=false
HARNESS_ORCHESTRATOR_MAX_STEP_RETRY=2
HARNESS_ORCHESTRATOR_MAX_REPLAN=2
HARNESS_ORCHESTRATOR_PLANNER_MAX_RETRIES=2
HARNESS_ORCHESTRATOR_STEP_MAX_STEPS=10
```

- [ ] **步骤 4：最终 Commit**

```bash
git add .env.example
git commit -m "docs: .env.example 补充编排器配置项"
```

---

## 附录 · 关键类型与签名一览（供跨任务对照，防命名漂移）

```
# app/orchestration/plan.py
Artifact(summary: str, data: dict = {}, files: list[str] = [])
PlanStep(id, description, expected, depends_on=[], status="pending", result: Artifact|None=None, attempts=0)
Plan(goal: str, steps: list[PlanStep]=[], version: int=1)
Verdict(ok: bool, reason: str)
Review(accept: bool, feedback: str)
validate_plan(steps: list[PlanStep]) -> str | None
ready_steps(plan: Plan) -> list[PlanStep]
has_pending(plan: Plan) -> bool

# app/orchestration/planner.py
Planner(complete, *, max_retries=2).plan(goal) -> Plan
Planner(...).replan(goal, plan, feedback) -> Plan
PlannerError(RuntimeError)

# app/orchestration/critic.py
Critic(complete).validate(step, artifact: Artifact) -> Verdict
Critic(complete).review(goal, plan, artifacts: dict[str,Artifact]) -> Review

# app/orchestration/executor.py
Executor(client, registry, system_prompt, model, *, max_steps=10, budget=None, loop_detect_window=0)
Executor(...).execute(step, deps: dict[str,Artifact], hint="") -> async gen[Progress ... , StepArtifact]
StepArtifact(artifact: Artifact, error: str|None=None)

# app/orchestration/orchestrator.py
Orchestrator(*, client, registry, model, planner, critic, executor,
             fast_complete, budget=None, max_step_retry=2, max_replan=2)
Orchestrator(...).run(user_message) -> async gen[Event]   # 仅现有 Event 类型
```

**注入契约（供测试替身对齐）：** `complete` 签名恒为 `async (system: str, user: str) -> str`；`fast_complete` 同签名。`call_json(complete, system, user) -> dict` 强制 JSON。

# Plan-Execute-Reflect 多 Agent 编排器 · 设计规格

- 日期：2026-07-18
- 状态：设计已评审，待写实现计划
- 分支：worktree-plan-execute-reflect（PR 目标 dev）

## 1. 背景与目标

当前主流程是单 agent 的 **ReAct 循环**（`harness/loop/agent_loop.py::AgentLoop`）：模型每一步自己决定下一个工具，执行、回灌、循环，直到不再要工具或撞 `max_steps`。现有的"规划"（`app/tools/plan_tool.py` 的 `update_plan` + `PLAN_SYSTEM_GUIDANCE`）只是**提示模型维护一份给用户看的清单**，这份 plan **不驱动控制流**；`DispatchTool` 是模型可即兴调用的派发**工具**，不是计划驱动的确定性编排；系统**没有任何反思/把关环节**。

由此带来四个已确认的痛点，本设计逐一解决：

1. **复杂任务跑偏/没章法** —— 引入前置**规划**（Planner 一次性产出全局计划）
2. **结果质量没人把关** —— 引入**分层反思**（单步校验 + 终局 Critic 把关）
3. **步骤无法并行** —— 计划用 **DAG** 表达依赖，无依赖步并发执行
4. **分工不清、上下文混** —— 每步交给独立上下文的 **Executor**，只看得到自己的子任务与依赖产物

### 核心决策（评审结论）

| 决策点 | 结论 |
|---|---|
| "不要 ReAct"的边界 | **只换外层控制器**：顶层改为确定性 Plan→Execute→Reflect 控制器；步骤内部仍由 `AgentLoop` 工具循环执行 |
| 落位方式 | **直接替换主流程**：`chat.py` 改接 Orchestrator；`AgentLoop` 保留，降级为 Executor 引擎；**保留简单问答短路** |
| Agent 角色 | **固定三角色**：Planner / 通用 Executor（拿全部工具）/ Critic；不分领域，不复用 dispatch roster |
| 反思粒度 | **分层**：每步轻量校验（可重试单步）+ 全部完成后终局 Critic 把关（不过关则重规划） |
| 控制循环 | **方案 A**：静态计划 + DAG 并行执行 + 分层反思 + 有界重规划（经典 Plan-and-Execute） |
| 计划结构 | **DAG**（步骤带 `depends_on` 依赖边） |

### 非目标（YAGNI）

- 分层递归规划（Planner 把步骤再拆子计划）—— 暂不做
- 领域专职 worker 分工（复用 dispatch roster）—— 暂不做，固定三角色
- 增量式/边走边规划（会往 ReAct 即兴模式漂）—— 明确排除
- 并行步"跑到一半崩"的 resume 续跑 —— v1 不做（语义复杂），仅在步边界存 checkpoint
- 反思环节的前端专属徽章 —— 可选锦上添花，v1 走通用进度行

## 2. 架构分层与模块边界

新增确定性控制器 `Orchestrator`，放在 **`app/orchestration/`**（与 `app/completion.py`、`app/verify.py` 同属 app 层组合逻辑）。

> **落位修正（实现阶段发现）**：规格初稿设想放 `harness/orchestration/`，但 Planner/Critic 需要结构化 JSON 输出，唯一的 JSON 强制+解析设施 `call_json` 在 `app/verify.py`（app 层）。放 harness 会造成 `harness → app` 反向依赖、破坏分层。现有 `dispatch.py` 能待在 harness 是因其子 agent 产出自由文本、无需 `call_json`。故编排器落 `app/orchestration/`，harness 保持纯净。

```
app/api/chat.py
      │  调 orchestrator.run(user_msg) —— 返回与 AgentLoop 相同的 Event 流
      ▼
┌─────────────────────────────────────────────────────────┐
│  Orchestrator  (harness/orchestration/orchestrator.py)   │
│  确定性控制器：短路判定 → 规划 → 调度 → 反思 → 定稿      │
│                                                          │
│  ├── Triage    简单问答短路（一次快速分类调用）           │
│  ├── Planner   goal → Plan(DAG)          planner.py       │
│  ├── Scheduler 纯代码，按依赖出就绪步     （控制器内）      │
│  ├── Executor  执行单步 = 包一个 AgentLoop executor.py    │
│  │             （通用 prompt + 全工具 + 隔离上下文）       │
│  ├── Critic    validate(步) / review(全局) critic.py      │
│  └── synthesize 汇总 done 步 → 流式最终答复（控制器内）     │
└─────────────────────────────────────────────────────────┘
```

**关键复用**：Executor 不是新引擎 —— 每个步骤起一个现有 `AgentLoop` 实例（通用 system prompt + 全量工具池 + 独立 `ContextManager`）。步骤内部的工具循环仍由久经考验的 `AgentLoop` 跑。新增的只是"外层控制器"。

### 单元接口（边界清晰，可独立测试）

| 单元 | 接口 | 产出 |
|---|---|---|
| Planner | `plan(goal, ctx) -> Plan` / `replan(goal, plan, feedback) -> Plan` | 结构化 LLM 调用，出/改 DAG |
| Scheduler | 纯代码 `ready(plan) -> list[PlanStep]` | DAG 拓扑 → 就绪步集合 |
| Executor | `execute(step, deps: dict[str, Artifact]) -> AsyncIterator[Event] + Artifact` | 包 AgentLoop，事件流 + 最终产物 |
| Critic | `validate(step, artifact) -> Verdict` | 轻量校验：ok + 原因 |
| Critic | `review(goal, plan, artifacts) -> Review` | 终局：accept/replan + 反馈 |
| synthesize | `synthesize(goal, artifacts) -> AsyncIterator[TextDelta] + str` | 一次汇总调用，流式最终答复 |

**落位**：`app/assembly.py` 组装 `Orchestrator`（复用现有 client、工具池 `pool`、completer）；`app/api/chat.py` 改调 `orchestrator.run()`。现有 `AgentLoop` 保留为 Executor 引擎与 triage 短路引擎。

## 3. 数据结构与 DAG schema

放在 `harness/orchestration/plan.py`。步骤全交给通用 Executor，故**无角色字段**。

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal

StepStatus = Literal["pending", "running", "done", "failed", "skipped"]  # 复用现有词汇

@dataclass
class Artifact:                          # 结构化步骤产出
    summary: str                         # 喂给下游 prompt / 汇总的简短摘要
    data: dict = field(default_factory=dict)        # 结构化键值，下游步按需取
    files: list[str] = field(default_factory=list)  # 沙箱内产物文件路径（如生成的代码）

@dataclass
class PlanStep:
    id: str                              # 稳定短 id，如 "s1"，供依赖引用
    description: str                     # 交给 Executor 的子任务描述
    expected: str                        # 预期产出：该步应交出什么，供 Critic 比对
    depends_on: list[str] = field(default_factory=list)  # 依赖步 id → DAG 边
    status: StepStatus = "pending"
    result: Artifact | None = None       # 结构化产物
    attempts: int = 0                    # 已尝试次数，供有界重试

@dataclass
class Plan:
    goal: str                            # 原始用户目标，重规划/反思时的锚
    steps: list[PlanStep] = field(default_factory=list)
    version: int = 1                     # 每次重规划 +1

@dataclass
class Verdict:                           # Critic.validate 单步校验产出
    ok: bool
    reason: str

@dataclass
class Review:                            # Critic.review 终局把关产出
    accept: bool
    feedback: str                        # 不过关时喂回 Planner 的改进意见

@dataclass
class OrchestratorState:                 # 可 checkpoint（对齐 RunState 思路）
    run_id: str
    plan: Plan
    replan_count: int = 0
```

**Planner 结构化输出 schema**（pydantic `BaseModel`，与现有 tool `Params` 一致）：

```python
class _PlanStepOut(BaseModel):
    id: str
    description: str
    expected: str
    depends_on: list[str] = []
class _PlannerOutput(BaseModel):
    steps: list[_PlanStepOut]
```

**落地即校验**（仿 `evals/dataset.py`：重复 id 直接报错）：

1. `id` 全局唯一
2. `depends_on` 每个元素必须指向已存在的 `id`（无悬空依赖）
3. 拓扑排序必须成功（**无环**）

三条任一不满足 → 拒绝该计划并让 Planner 重出（有界重试，见 §5）。守住这三条，Scheduler 永不死锁。

**DAG 如何表达并行**：边即 `depends_on`。Scheduler 每轮取"就绪集" = 所有依赖已 `done` 且自身 `pending` 的步；这一批用 `asyncio.gather` 并发执行。无依赖关系的步天然同批并行。

**步骤间传数据**：某步依赖前置步时，把前置步的 `Artifact`（结构化 `data` + `summary`）注入该步 Executor 的 prompt。Executor 各自独立 `ContextManager`，只看得到自己的子任务 + 依赖产物 —— 即上下文隔离。

**前端兼容**：Plan 往 `Progress(scope="plan")` emit 时转成现有 `[{title, status}]` 形状（`title=description`），现成 plan UI 直接渲染。DAG 依赖是内部信息，暂不在 UI 画。

## 4. 控制流（确定性循环）

`Orchestrator.run()` 是 async 生成器，**产出与 `AgentLoop` 完全相同的 Event 类型**，故 chat API 与前端几乎不用改。

```
run(user_message):
  yield RunStarted
  ── 1. Triage 短路 ────────────────────────────────
  若 triage(msg)=简单:                    # 一次快速分类调用（fast-completer）
     复用单个 AgentLoop 直答 → 透传其事件 → RunFinished → return

  ── 2. Plan ───────────────────────────────────────
  plan = Planner.plan(goal)              # 校验(唯一id/依赖存在/无环)失败则有界重试
  yield Progress(scope="plan", 转成 [{title,status}])

  ── 3. 执行循环（调度 + 单步校验）─────────────────
  while 存在 pending 步:
     ready = Scheduler.ready(plan)        # 依赖全 done 且自身 pending 的步
     if ready 为空 and 仍有 pending:       # 死锁/无进展保护
        yield RunError("计划无法推进：存在无法满足的依赖"); return
     results = await gather(              # 这一批并发跑，各自独立上下文
        Executor.execute(s, 依赖步的 Artifact) for s in ready,
        return_exceptions=True)          # 一步崩不拖累同批兄弟
     for (step, artifact_or_exc) in results:
        若为异常: 当 validate 失败处理
        verdict = Critic.validate(step, artifact)   # 拿 step.expected 比对
        if verdict.ok:
           step.status=done; step.result=artifact
        else:
           step.attempts += 1
           step.status = pending if attempts<MAX_STEP_RETRY else failed
           # 重试时把 verdict.reason 作为反馈注入下一次 Executor
     yield Progress(scope="plan", 更新后)
     # 某步 failed → 依赖它的步永远进不了 ready → 交给终局 Critic 判

  ── 4. 终局 Critic 把关 ───────────────────────────
  review = Critic.review(goal, plan, 所有 done 的 Artifact)
  if review.accept or replan_count >= MAX_REPLAN:
     final = synthesize(goal, artifacts)  # 一次汇总调用，流式吐 TextDelta
     yield RunFinished(final); return
  else:
     replan_count += 1
     被废弃的剩余步 → status=skipped
     plan = Planner.replan(goal, 已done步, review.feedback)  # 保留成果，只重排剩余
     goto 3
```

### 确定性护栏（代码级，不靠模型自觉）

| 护栏 | 作用 | 默认（可配） |
|---|---|---|
| `MAX_STEP_RETRY` | 单步反复失败上限 | 2 |
| `MAX_REPLAN` | 终局重规划轮数上限 | 2 |
| 死锁保护 | 有 pending 但无就绪步 → 中止 | 硬判 |
| `BudgetTracker` | 步边界查 token/时长预算 | 复用现有 |

**降级策略**：某步彻底 `failed` → 让依赖链自然断掉 + 交终局 Critic 决定重规划或带残缺定稿（已认可）。

## 5. 错误处理与预算

遵循项目现有"**线上 fail-open、离线 fail-closed**"哲学（`app/verify.py` 抖动吞掉当通过、`evals/scorers.py` 抖动显式报错）。编排器是**线上路径**，基建抖动一律 fail-open，绝不因判官调用失败拦交付。

| 失败点 | 处理 | 依据 |
|---|---|---|
| **预算超限** | `BudgetTracker` 每轮调度前 + 每次 Planner/Critic/synthesize 调用前查；超限 → 用已完成步尽力 `synthesize` 定稿 | 复用 `BudgetTracker`，残缺胜过空手 |
| **Planner 出无效 DAG** | 带错误反馈有界重试（2 次）；仍失败 → **降级为单个 AgentLoop 直答** | 优雅降级 |
| **单步执行抛异常** | `gather(return_exceptions=True)`：一步崩不拖累同批；崩的步当 validate 失败，走重试 | 隔离故障 |
| **Critic 调用抖动** | **fail-open**：validate 出错当通过、review 出错当 accept，记一条 `Progress` 说明 | 对齐 `app/verify.py` |
| **死锁**（有 pending 无就绪步） | 硬判 `RunError` | §4 护栏 |
| **重规划打转** | `MAX_REPLAN=2` 兜底；到顶带现有成果定稿 | 有界 |

**只有两种情况真的 `RunError`**：死锁；Planner 重试耗尽后连降级单跑也失败。其余一律尽量给用户一个（可能残缺但有说明的）答复。

**checkpoint**：步边界存 `OrchestratorState`（复用 `CheckpointStore` 思路）。**resume 续跑 v1 不做**（并行步"跑到一半崩"续跑语义复杂，与现有 `AgentLoop.resume` 的"有副作用工具可能重执行"同源坑），先把正向流跑扎实。

## 6. 事件与前端复用

**核心原则：Orchestrator 只发现有的 Event 类型。** `event_to_dict`（`harness/persistence/serialize.py`，isinstance 白名单）→ SSE `_sse` → 前端全链路不改。新增事件类型会在 `event_to_dict` 走 `else` 分支丢 `data`，故**不加新事件**。

| 编排阶段 | 复用事件 | 前端现状 |
|---|---|---|
| run 开始/结束/出错 | `RunStarted` / `RunFinished` / `RunError` | 现成 |
| 计划展示与更新 | `Progress(scope="plan")`，转 `[{title,status}]` | 现成 plan UI 直接渲染 |
| 步骤内工具活动 | `ToolStarted/Finished` + `set_current_agent("executor:s1")` → `Progress(scope="subagent:...")` | 与 dispatch 一致，现成折叠渲染 |
| 反思/校验提示 | `Progress(scope="reflect")` | 走通用 progress 行 |
| 最终答复 | `synthesize` 流式 `TextDelta` | 现成 |
| 模型用量/审批 | `ModelUsage` / `ApprovalRequired` | 现成 |

唯一"新"的是 `scope="reflect"` 字符串值（非新事件类型，序列化天然支持）。前端当普通 progress 行显示即可；专属徽章为可选。

**chat.py 简化（新路径）**：现有 app 级 shim 被编排器吸收 ——
- `_finalize_stale_plan`（逼模型收尾清单）→ 编排器状态机天然保证每步有终态，不再需要
- verify 交付门 → 被**终局 Critic**取代，且更强（Critic 能触发重规划，verify 门只能拦）

新路径上 `chat.py` 瘦成薄驱动：组装 orchestrator → pump 事件到 SSE → 落轨迹。上述 shim 在新路径停用，代码先保留（triage 短路的单 AgentLoop 路径可能仍用得上部分）。

## 7. 测试策略

严格遵循 **TDD（测试先行）** 与**双层测试纪律**（CI 全 mock 确定性 / 真实端点手动验收）。

**第一层 · 控制器单元与 mock 测试（进 CI，秒级确定性）** —— 必须项，用脚本化 LLM（`tests/test_conftest_mock.py`）+ 假工具：

- **Scheduler 纯代码单测**：拓扑序、就绪集、并行批、环检测、悬空依赖拒绝
- **控制流 mock 测试**：脚本化 Planner 返回已知 DAG + 假 Executor 返回预设 Artifact + 脚本化 Critic，断言：并行批执行、validate 失败重试、review 拒绝触发重规划、废弃步标 `skipped`、死锁检测、有界重试/重规划到顶、triage 短路
- **fail-open 断言**：Critic 调用抛异常时步骤照常推进
- **事件序列断言**：断言 emit 的 Event 序列符合预期（同时守住前端兼容性）

**第二层 · 真实端点集成测试（无 key 时 skip）** —— 仿 `tests/test_integration_real.py`：一个端到端编排任务跑通。

**第三层 · 手动验收 demo** —— 新增 `examples/orchestrator_demo.py`（顺带填上"编排层无 demo"缺口）。

**可选 · eval 门禁**：编排器组件级套件进 `evals/`（全 mock 确定性），让控制器逻辑回归卡 CI —— 与现有 eval 纪律一致，非必须，先不阻塞主线。

## 8. 影响面与新增文件

**新增（均在 app 层，见 §2 落位修正）：**
- `app/orchestration/plan.py` —— 数据结构 + 纯 DAG 函数（校验/就绪集）（§3）
- `app/orchestration/planner.py` —— Planner
- `app/orchestration/executor.py` —— Executor（包 harness `AgentLoop`）
- `app/orchestration/critic.py` —— Critic
- `app/orchestration/orchestrator.py` —— 控制器（含 Scheduler 调度、triage、synthesize）
- `tests/test_orchestration_*.py` —— 控制器单元/mock 测试
- `examples/orchestrator_demo.py` —— 手动验收

**改动：**
- `app/assembly.py` —— 组装 Orchestrator
- `app/api/chat.py` —— 主路径改调 orchestrator.run()，停用被吸收的 shim
- 新增若干 config 项（`MAX_STEP_RETRY` / `MAX_REPLAN` / triage 开关等）

**保留不动：**
- `harness/loop/agent_loop.py`（降级为 Executor 引擎 + triage 短路引擎）
- `harness/events.py`、`harness/persistence/serialize.py`（不加新事件）
- 前端（复用现有事件与 Progress 通道）
- `harness/orchestration/dispatch.py`、`spec.py`、roster（本设计不复用，但不删除）

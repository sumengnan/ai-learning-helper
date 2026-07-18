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
    Progress, RunFinished, RunStarted, TextDelta,
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
    """把 Plan 转成前端 plan UI 认的 [{id,title,status}] 形状，走 Progress(scope=plan)。

    带上 id：前端据此把 executor:<id> 的执行明细（工具调用）挂到对应计划步下，合并成一棵树。
    """
    steps = [{"id": s.id, "title": s.description, "status": s.status,
              "depends_on": list(s.depends_on)} for s in plan.steps]
    return Progress(scope="plan", text=json.dumps(steps, ensure_ascii=False), key="plan")


class Orchestrator:
    def __init__(self, *, client, registry: ToolRegistry, model: str,
                 planner: Planner, critic: Critic, executor: Executor,
                 fast_complete, budget=None, budget_factory=None,
                 max_step_retry: int = 2, max_replan: int = 2) -> None:
        self._client = client
        self._registry = registry
        self._model = model
        self._planner = planner
        self._critic = critic
        self._executor = executor
        self._fast_complete = fast_complete
        self._budget = budget                # 直接注入的预算实例（主要供测试）
        # 每次 run() 新建预算的工厂：编排器是单例，用工厂产出每轮独立的 BudgetTracker，
        # 避免跨轮累加、且并发安全（预算以局部变量贯穿一次 run，绝不写回 self）。
        self._budget_factory = budget_factory
        self._max_step_retry = max_step_retry
        self._max_replan = max_replan

    # ---- triage ----
    async def _is_simple(self, message: str) -> bool:
        try:
            raw = await self._fast_complete(TRIAGE_SYSTEM, message)
            return raw.strip().lower().startswith("simple")
        except Exception:
            return False   # 判不了就走完整编排（宁可多做不可少做）

    async def _simple_answer(self, message: str, budget=None):
        """简单问答短路：单个全能力 AgentLoop 直答，透传其事件（跳过其 RunStarted，避免重复）。"""
        loop = AgentLoop(client=self._client, registry=self._registry,
                         context=ContextManager(SYNTH_SYSTEM),
                         max_steps=10, budget=budget, model_name=self._model)
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
        # 每次 run 新建独立预算（工厂优先），以局部变量贯穿本轮——单例并发安全、不跨轮累加
        budget = self._budget_factory() if self._budget_factory else self._budget
        if budget:
            budget.start()

        if await self._is_simple(user_message):
            async for ev in self._simple_answer(user_message, budget):
                yield ev
            return

        try:
            plan = await self._planner.plan(user_message)
        except PlannerError:
            async for ev in self._simple_answer(user_message, budget):   # 降级
                yield ev
            return
        yield _plan_progress(plan)

        replan_count = 0
        retry_hints: dict[str, str] = {}
        # 跨轮累积 done 产物：replan 返回全新 Plan（旧 done 步不在其中），必须在换 plan 前收走，
        # 否则终局 synthesize/review 只剩最后一轮的产物 —— 违反 spec §4「保留成果」并使闭环残废。
        all_artifacts: dict[str, Artifact] = {}
        while True:
            async for ev in self._schedule_rounds(plan, retry_hints, budget):
                yield ev
            for s in plan.steps:      # 收走本轮 done 产物（replan 换 plan 也不丢）
                if s.status == "done" and s.result:
                    all_artifacts[s.id] = s.result

            # 预算超限：不再规划，带现有成果尽力定稿（spec §5「残缺胜过空手」）
            if budget:
                try:
                    budget.check()
                except BudgetExceeded:
                    break

            review = await self._critic.review(user_message, plan, all_artifacts)
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

        # 无论产物多寡都尽力 synthesize：spec §5「其余一律尽量给用户一个（可能残缺但有说明的）
        # 答复」——零产物时 synthesize 也会据空产物说明未能完成，胜过硬判 RunError。
        # 累加 synthesize 吐出的 TextDelta 即最终文本——不用侧信道，真/假 _synthesize 都适用
        final_parts: list[str] = []
        async for ev in self._synthesize(user_message, all_artifacts):
            if isinstance(ev, TextDelta):
                final_parts.append(ev.text)
            yield ev
        final = "".join(final_parts) or "（未能生成答复）"
        yield RunFinished(message=Message(role=Role.ASSISTANT, content=final))

    # ---- 调度：一轮轮跑就绪集，直到无 pending、预算超限或无法推进（后两者带现有成果收尾）----
    async def _schedule_rounds(self, plan: Plan, retry_hints: dict[str, str], budget=None):
        while has_pending(plan):
            if budget:
                try:
                    budget.check()
                except BudgetExceeded:
                    # 预算超限：停跑本轮，剩余未完成步标 skipped，带现有成果交终局（spec §5）。
                    # 不硬判 RunError —— run() 会据现有产物尽力 synthesize。
                    self._skip_unfinished(plan)
                    yield Progress(scope="reflect", text="预算超限，带现有成果收尾")
                    return
            ready = ready_steps(plan)
            if not ready:
                # 有 pending 但无就绪步：validate_plan 已保证 DAG 合法可调度，故运行期唯一现实成因
                # 是某步重试耗尽被判 failed、其后继依赖永远无法满足。按 spec §4「交终局 Critic 判」，
                # 把这些不可达步标 skipped、正常收尾（带残缺成果），不再硬判 RunError 丢弃已完成工作。
                self._skip_unfinished(plan)
                return
            for s in ready:
                s.status = "running"
            # 就绪步开跑即发一次快照：否则顶部任务步骤从 pending 直接跳 done，中途不显示进行态、不转圈
            yield _plan_progress(plan)
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
            try:
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
            finally:
                # 提前放弃迭代（客户端断连/停止 → 本生成器 aclose，GeneratorExit 抛在 yield 处）
                # 时，同批未完成的 worker 必须取消，否则会变成继续跑 LLM 的悬挂任务。正常跑完时
                # 所有 task 已 done，cancel 是空操作、gather 立即返回，零额外开销。
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            yield _plan_progress(plan)

    @staticmethod
    def _skip_unfinished(plan: Plan) -> None:
        """把仍 pending/running 的步标 skipped —— 无法再推进时收尾，done/failed 保持不动。"""
        for s in plan.steps:
            if s.status in ("pending", "running"):
                s.status = "skipped"

    def _on_step_fail(self, step, retry_hints: dict[str, str], reason: str) -> None:
        step.attempts += 1
        if step.attempts < self._max_step_retry:
            step.status = "pending"        # 重试：回到就绪集
            retry_hints[step.id] = reason
        else:
            step.status = "failed"         # 放弃：依赖链自然断掉，交终局 Critic 判
            retry_hints.pop(step.id, None)

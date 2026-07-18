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

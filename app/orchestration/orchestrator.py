# app/orchestration/orchestrator.py
"""Orchestrator：确定性 Plan→Execute→Reflect 控制器。

产出与 AgentLoop 完全相同的 Event 类型（RunStarted/Progress/TextDelta/RunFinished/RunError），
故 event_to_dict/SSE/前端零改动。控制流见 spec §4。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid


def _now_ms() -> int:
    return int(time.time() * 1000)


def _elapsed(step) -> int:
    """本步耗时（毫秒）；无起点则算 0。"""
    return _now_ms() - step.started_at_ms if step.started_at_ms else 0

from harness.context.manager import ContextManager
from harness.events import (
    ModelUsage, Progress, ReasoningDelta, RunFinished, RunStarted, TextDelta,
)
from harness.loop.agent_loop import AgentLoop
from harness.reliability.budget import BudgetExceeded
from harness.tools.base import ToolRegistry
from harness.types import Message, Role

from .critic import Critic
from .executor import CLARIFY_GUIDE, Executor, HidingRegistry, StepArtifact
from .planner import Planner, PlannerError
from .plan import Artifact, Plan, has_pending, ready_steps
from .usage_ctx import (
    UsageAcc, record_usage, reset_acc, reset_reason_sink, set_acc, set_reason_sink,
)

TRIAGE_SYSTEM = (
    "判断用户消息是否为简单问答（打招呼、寒暄、单句事实、闲聊）。"
    "多步任务、需要检索/代码/工具、需要规划的一律算复杂。"
    "只回一个词：simple 或 complex。"
)

# 无需 LLM 判别的「明显简单」：整条消息就是纯寒暄/致谢/短应答。命中即跳过 triage 的那次模型调用，
# 直接走简单直答（省一次调用/延迟）。只放高置信度社交短句，且要求整条消息就是这些词、无其它实质
# 内容——带任务的消息（如「你好，帮我查资料」含"帮我"）不会命中，仍交 LLM triage。误判为简单的代价
# 也低：simple_answer 本就是全工具 ReAct，只是少做一次规划。
_GREETING_RE = re.compile(
    r"^[\s，。,.!！?？~、]*"
    r"(你好+|您好|哈喽|嗨|hi|hello|hey|在吗|在不在|早|早上?好|中午好|下午好|晚上好|晚安|"
    r"谢谢+|多谢|感谢|thanks|thank\s*you|thx|ok|okay|好的?|好嘞|行|嗯+|哦+|噢+|收到|"
    r"辛苦了?|不错|棒|赞|再见|拜拜|bye|goodbye)"
    r"[\s，。,.!！?？~、]*$",
    re.IGNORECASE)


def _obvious_simple(message: str) -> bool:
    """启发式短路：纯寒暄/致谢/短应答无需 LLM triage。高精度优先，宁可漏判（回退 LLM）不可误判。"""
    m = (message or "").strip()
    return bool(m) and len(m) <= 20 and _GREETING_RE.match(m) is not None

SYNTH_SYSTEM = (
    "你是汇总员。根据用户目标和各步骤的产出，写出面向用户的最终答复。"
    "只使用已给出的产出，不要编造；条理清晰、直接作答。"
)


def _synth_user(goal: str, artifacts: dict[str, Artifact], recent_dialogue: str = "") -> str:
    lines = []
    if recent_dialogue:   # 带上最近对话，最终答复才有多轮上下文（指代/追问/延续先前话题）
        lines.append(f"最近对话（供理解上下文与延续语气）：\n{recent_dialogue}\n")
    lines += [f"用户目标：\n{goal}\n", "各步骤产出："]
    for sid, art in artifacts.items():
        lines.append(f"[{sid}] {art.summary}")
    lines.append("\n请综合以上，写出对用户的最终答复。")
    return "\n".join(lines)


def _plan_progress(plan: Plan) -> Progress:
    """把 Plan 转成前端 plan UI 认的 [{id,title,status}] 形状，走 Progress(scope=plan)。

    带上 id：前端据此把 executor:<id> 的执行明细（工具调用）挂到对应计划步下，合并成一棵树。
    """
    steps = [{"id": s.id, "title": s.description, "status": s.status,
              "depends_on": list(s.depends_on),
              "started_at_ms": s.started_at_ms, "elapsed_ms": s.elapsed_ms}
             for s in plan.steps]
    return Progress(scope="plan", text=json.dumps(steps, ensure_ascii=False), key="plan")


class Orchestrator:
    def __init__(self, *, client, registry: ToolRegistry, model: str,
                 planner: Planner, critic: Critic, executor: Executor,
                 fast_complete, fast_client=None, fast_model: str | None = None,
                 fast_max_prompt_tokens: int = 0,
                 budget=None, budget_factory=None,
                 max_step_retry: int = 2, max_replan: int = 2) -> None:
        self._client = client
        self._registry = registry
        self._model = model
        self._planner = planner
        self._critic = critic
        self._executor = executor
        self._fast_complete = fast_complete
        # 简单直答走快速档模型（省钱/提速）：未配 fast_model 时 build_fast_client 回退主 client/主模型，
        # 故这里回退 self._client/self._model —— 没配快速模型即零行为变更。
        self._fast_client = fast_client if fast_client is not None else client
        self._fast_model = fast_model or model
        # 简单直答的上下文按快速模型口径再收一道（>0 时启用）：base_ctx 是按主模型预算裁的，
        # 快速模型窗口更小时据此确定性重裁，防溢出。0=不裁（默认）。
        self._fast_max_prompt_tokens = int(fast_max_prompt_tokens or 0)
        self._budget = budget                # 直接注入的预算实例（主要供测试）
        # 每次 run() 新建预算的工厂：编排器是单例，用工厂产出每轮独立的 BudgetTracker，
        # 避免跨轮累加、且并发安全（预算以局部变量贯穿一次 run，绝不写回 self）。
        self._budget_factory = budget_factory
        self._max_step_retry = max_step_retry
        self._max_replan = max_replan

    @staticmethod
    async def _plan_streaming(coro, out: dict):
        """跑一次 planner 调用，把它"先思考再出严格 JSON"里的思考**实时**转成
        Progress(scope=plan_reasoning) 流式 yield（不是等规划完再一次性发）；
        规划结果写进 out["plan"]，PlannerError 写进 out["error"]。

        用后台任务 + 队列：planner 在 task 里跑（继承本上下文里挂的 reason sink），思考经
        sink → 队列，本生成器边收边 yield，实现流式。提前放弃迭代时取消 task，避免悬挂。"""
        queue: asyncio.Queue = asyncio.Queue()
        sentinel = object()

        async def _worker():
            try:
                out["plan"] = await coro
            except PlannerError as e:
                out["error"] = e
            finally:
                queue.put_nowait(sentinel)

        token = set_reason_sink(queue.put_nowait)
        t0 = _now_ms()
        saw_reasoning = False
        task = asyncio.create_task(_worker())
        try:
            while True:
                item = await queue.get()
                if item is sentinel:
                    break
                saw_reasoning = True
                yield Progress(scope="plan_reasoning", text=item)
        finally:
            reset_reason_sink(token)
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if saw_reasoning:   # 末尾发规划思考耗时（落 progress 列，刷新后可还原耗时）
            yield Progress(scope="plan_reasoning", text="", key="__plan_reasoning_elapsed__",
                           detail={"elapsed_ms": _now_ms() - t0})

    # ---- triage ----
    async def _is_simple(self, message: str) -> bool:
        try:
            raw = await self._fast_complete(TRIAGE_SYSTEM, message)
            return raw.strip().lower().startswith("simple")
        except Exception:
            return False   # 判不了就走完整编排（宁可多做不可少做）

    async def _simple_answer(self, message: str, budget=None, *, context=None, registry=None,
                             prefer_main: bool = False):
        """简单问答短路：单个全能力 AgentLoop 直答，透传其事件（跳过其 RunStarted，避免重复）。

        承载绝大多数流量（问答/追问/考试）。context 为本轮每请求上下文（系统提示+全部指引+会话
        历史+记忆，由 chat 路由传入）——多轮对话、附件/考试/引用/日期/个性化全靠它；缺省回退到最小
        SYNTH_SYSTEM+澄清指引（测试/back-compat）。registry 为本轮每请求工具表（含用户级工具）。

        prefer_main：用主 client/model 而非快速档。用于有状态交互（考试）——这类流程指令繁杂，
        需可靠地按系统注入的「[考试系统判定]…请呈现下一题」提示逐题推进；快速档小模型常漏掉
        「呈现下一题」这一步（编排器化之前考试本就跑在主模型上，属回归修复）。且主档窗口更大，
        无需按快速档再收窗口——否则携带下一题文本的最后一条消息可能被 Clamp 截断而丢题。"""
        ctx = context if context is not None else ContextManager(SYNTH_SYSTEM + CLARIFY_GUIDE)
        client = self._client if prefer_main else self._fast_client
        model = self._model if prefer_main else self._fast_model
        # 按快速模型口径重裁（>0 时）：仅在真用快速档时才收——主档 base_ctx 已按主模型裁好
        if context is not None and not prefer_main and self._fast_max_prompt_tokens > 0:
            from harness.context.clamp import ClampedContextManager
            ctx = ClampedContextManager(ctx, self._fast_model, self._fast_max_prompt_tokens)
        loop = AgentLoop(client=client,
                         registry=registry if registry is not None else self._registry,
                         context=ctx, max_steps=10, budget=budget, model_name=model)
        async for ev in loop.run(message):
            if isinstance(ev, RunStarted):
                continue
            yield ev

    # ---- synthesize ----
    async def _synthesize(self, goal: str, artifacts: dict[str, Artifact], recent_dialogue: str = ""):
        """流式汇总最终答复。yield TextDelta（run() 累加得最终文本）+ ReasoningDelta（开思考模式时
        把最终答复的思考过程透传给前端——编排器路径唯一该展示思考的地方）。

        保持纯生成（空工具表、单步）以免在汇总阶段又去调工具；带上 recent_dialogue 让最终答复有
        多轮上下文。"""
        loop = AgentLoop(
            client=self._client, registry=ToolRegistry(),
            context=ContextManager(SYNTH_SYSTEM), max_steps=1, model_name=self._model)
        final = ""
        streamed = False
        async for ev in loop.run(_synth_user(goal, artifacts, recent_dialogue)):
            if isinstance(ev, TextDelta):
                streamed = True
                yield ev
            elif isinstance(ev, ReasoningDelta):   # 思考过程透传（前端 ThinkingBlock 展示）
                yield ev
            elif isinstance(ev, ModelUsage):       # 用量记进累加器，run() 末尾汇总
                record_usage(ev.usage, ev.cost_usd, ev.model)
            elif isinstance(ev, RunFinished):
                final = ev.message.content or ""
        # 端点未流式（只在 RunFinished 给全量）时，补一个 TextDelta，保证 run() 能累加到文本
        if final and not streamed:
            yield TextDelta(text=final)

    # ---- 主入口 ----
    async def run(self, user_message: str, verify: bool = True, *,
                  context=None, registry=None, recent_dialogue: str = "",
                  force_simple: bool = False, run_id: str | None = None):
        """verify：对应前端结果校验开关。开 → 终局 Critic 把关 + 可重规划；关 → 跑完一轮
        直接汇总交付，不做终局 review/重规划（更快，但不把关）。

        force_simple：强制走简单直答（ReAct 单循环），跳过 triage 与 plan-execute-synthesize。
        用于「有状态、多轮、模型驱动」的交互流程——典型是模拟考试：模型调 start_exam 拿到题、
        同一轮原样呈现、下一轮由服务端 grade_exam_turn 拦截判分。这类流程只适合单循环：若被拆成
        多步再汇总，start_exam 的原样呈现指令会被执行子步/终局汇总两层概括吞掉，且 Critic 判某步
        不合格触发重试会再次 start_exam、把考试进度重置。chat 路由在命中考试语境（注入 EXAM_GUIDE）
        时置真。

        每请求依赖（由 chat 路由传入，使编排器可作为唯一主流程而不丢失既有能力）：
        - context：本轮上下文（系统提示+全部指引+会话历史+记忆）。用于简单直答与最终汇总——
          多轮对话、附件/考试/引用/日期/个性化全靠它。
        - registry：本轮每请求工具表（含用户级 save_download/知识库/考试/附件工具）。用于简单直答
          与各执行子步；执行子步会先隐藏 update_plan 再用。
        - recent_dialogue：最近对话文本，喂给 Planner（上下文相关的拆分）与最终汇总。
        缺省全为空/回退，保持对既有测试透明。"""
        # run_id 由 chat 路由传入（= 登记进 conversation_runs 的那个），使本轮所有事件经 sink
        # 落 trajectory 时都归到该 id 下——否则编排器自造 uuid、事件归了另一个 id，按用户过滤
        # 的运行统计（AI 在为我做什么/回答质量）会把它们全滤掉。缺省自造，保持对既有测试透明。
        run_id = run_id or uuid.uuid4().hex
        yield RunStarted(run_id=run_id)
        # 执行子步用的工具视图：每请求 registry 隐藏 update_plan（子步调它会覆盖总计划）；无则回退
        exec_reg = HidingRegistry(registry, {"update_plan"}) if registry is not None else None
        # 每次 run 新建独立预算（工厂优先），以局部变量贯穿本轮——单例并发安全、不跨轮累加
        budget = self._budget_factory() if self._budget_factory else self._budget
        if budget:
            budget.start()
        # 每轮独立用量累加器：planner/critic 的 completer、executor、synthesize 各子调用的
        # ModelUsage 都 record 到这里（含并行 worker，靠 contextvar 拷贝共享同一对象），
        # 末尾发一条总的，前端才显示得出总 tokens。
        acc = UsageAcc()
        acc_token = set_acc(acc)
        try:
            # 强制单循环（考试等有状态交互）优先，其次零成本短路（纯寒暄），最后才让 LLM 判简单/复杂。
            # force_simple 的场景（考试）走主模型（prefer_main）：需可靠逐题推进，快速档易漏「呈现下一题」。
            if force_simple or _obvious_simple(user_message) or await self._is_simple(user_message):
                async for ev in self._simple_answer(user_message, budget, context=context,
                                                    registry=registry, prefer_main=force_simple):
                    yield ev
                return

            out: dict = {}   # 边规划边流式发规划思考（顶部"任务计划思考"块），先于计划
            async for ev in self._plan_streaming(
                    self._planner.plan(user_message, recent_dialogue), out):
                yield ev
            if "error" in out:
                async for ev in self._simple_answer(user_message, budget,   # 降级
                                                    context=context, registry=registry):
                    yield ev
                return
            plan = out["plan"]
            yield _plan_progress(plan)

            replan_count = 0
            retry_hints: dict[str, str] = {}
            # 跨轮累积 done 产物：replan 返回全新 Plan（旧 done 步不在其中），必须在换 plan 前收走，
            # 否则终局 synthesize/review 只剩最后一轮的产物 —— 违反 spec §4「保留成果」并使闭环残废。
            all_artifacts: dict[str, Artifact] = {}
            while True:
                async for ev in self._schedule_rounds(plan, retry_hints, budget, exec_reg):
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

                if not verify:   # 结果校验关：跑完一轮直接汇总交付，不做终局 review/重规划
                    break

                # 结果校验过程可见（对应前端结果校验开关）：走 scope=verify，前端 VerifyBadge 据此
                # 显示「结果校验中…→通过/未通过」；不通过带缺口说明，重规划后会再发一轮，形成校验历史。
                yield Progress(scope="verify", text="结果校验中…", status="running")
                review = await self._critic.review(user_message, plan, all_artifacts)
                yield Progress(scope="verify",
                               text="结果校验通过" if review.accept
                                    else (review.feedback or "存在缺口，重新规划"),
                               status="ok" if review.accept else "error")
                if review.accept or replan_count >= self._max_replan:
                    break
                replan_count += 1
                for s in plan.steps:
                    if s.status in ("pending", "running"):
                        s.status = "skipped"
                out2: dict = {}   # 重规划思考也流式发，在新计划之前
                async for ev in self._plan_streaming(
                        self._planner.replan(user_message, plan, review.feedback), out2):
                    yield ev
                if "error" in out2:
                    break
                plan = out2["plan"]
                retry_hints = {}
                yield _plan_progress(plan)

            # 无论产物多寡都尽力 synthesize：spec §5「其余一律尽量给用户一个（可能残缺但有说明的）
            # 答复」——零产物时 synthesize 也会据空产物说明未能完成，胜过硬判 RunError。
            final_parts: list[str] = []
            async for ev in self._synthesize(user_message, all_artifacts, recent_dialogue):
                if isinstance(ev, TextDelta):
                    final_parts.append(ev.text)
                yield ev
            final = "".join(final_parts) or "（未能生成答复）"
            # 用量不再在此聚合发射：各子调用（executor/synthesize/planner/critic）的 record_usage
            # 已一路 emit 逐模型增量，经 chat 路由的 emitter 并入主流 → sink 落 trajectory（分模型
            # 历史统计）+ 前端（按模型累加得合计）。与 embedding/rerank 走同一条路，零重复。
            yield RunFinished(message=Message(role=Role.ASSISTANT, content=final))
        finally:
            reset_acc(acc_token)

    # ---- 调度：一轮轮跑就绪集，直到无 pending、预算超限或无法推进（后两者带现有成果收尾）----
    async def _schedule_rounds(self, plan: Plan, retry_hints: dict[str, str], budget=None,
                               exec_reg=None):
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
                s.started_at_ms = _now_ms()   # 计时起点：供前端进行中读秒、结束后算耗时
            # 就绪步开跑即发一次快照：否则顶部任务步骤从 pending 直接跳 done，中途不显示进行态、不转圈
            yield _plan_progress(plan)
            queue: asyncio.Queue = asyncio.Queue()

            async def _worker(step):
                deps = {d: nxt.result for d in step.depends_on
                        for nxt in plan.steps if nxt.id == d and nxt.result}
                art = None
                err = None
                try:
                    async for ev in self._executor.execute(
                            step, deps, retry_hints.get(step.id, ""), registry=exec_reg):
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
                        step.elapsed_ms = _elapsed(step)   # 定格耗时
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
            step.started_at_ms = None      # 清计时，重跑时重新起点
            retry_hints[step.id] = reason
        else:
            step.status = "failed"         # 放弃：依赖链自然断掉，交终局 Critic 判
            step.elapsed_ms = _elapsed(step)   # 定格耗时
            retry_hints.pop(step.id, None)

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

from app.today import with_today

from ..side_effects import empty_fx, has_any, tools_to_redo
from .critic import Critic
from .executor import CLARIFY_GUIDE, Executor, HidingRegistry, StepArtifact
from .planner import Planner, PlannerError, render_tool_roster
from .plan import (_KB_REQUESTED_RE, Artifact, Plan, PlanStep, file_saving_step_ids,
                   has_pending, ready_steps)
from .usage_ctx import (
    UsageAcc, record_usage, reset_acc, reset_reason_sink, set_acc, set_reason_sink,
)

# 结构化交付门留痕的 Progress key（chat 路由据此提取，写进 verify 列）
VERIFY_TRACE_KEY = "verify:trace"

TRIAGE_SYSTEM = (
    "判断用户**本次消息**是否为简单问答（打招呼、寒暄、单句事实、闲聊）。"
    "多步任务、需要检索/代码/工具、需要规划的一律算复杂。"
    # 只看孤立的一句话会把多步任务的追问判成简单：「再详细点」「那第三点呢」「继续」
    # 单独读起来都像闲聊，于是跳过规划、退化成一轮 ReAct，该拆的步骤没拆。
    "若给出了最近对话，务必据此判断本次消息是不是某个多步任务的延续——"
    "承接前文任务的追问（如「再详细点」「那第三点呢」「继续」「换个角度」）算复杂，"
    "哪怕它本身很短。只有与前文任务无关的寒暄/闲聊才算简单。"
    # 长文本容易被误读成「复杂」。但用户把原文/数据直接贴在消息里、只要求就地加工的，
    # 不需要拆步骤也不需要工具，正是简单直答该管的；判成复杂反而绕远路。
    "**用户已把要处理的原文/代码/数据贴在消息里、只需就地加工的**（翻译这段、总结这段、"
    "润色一下、解释这段代码、这段报错是什么意思），一律算简单——哪怕贴的内容很长。"
    "长度不是复杂度：要不要检索、要不要多步才是。\n"
    "只回一个词：simple 或 complex。"
)

# 喂给 triage 的最近对话上限。够判断「是不是在延续某个任务」即可，不必给全文——
# triage 每轮都跑，是最快那条路径上的固定开销。
_TRIAGE_DIALOGUE_MAX = 800

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


# 考试轮交给校验器的前置说明。_simple_answer_verified 只服务于考试轮
# （force_simple = bool(exam_guide)，见 chat.py），故可无条件附加。
_EXAM_REVIEW_NOTE = (
    "【本轮是考试进行中的一轮，请按考试规则审查】"
    "考试由服务端托管：题目一次只出一道，判分与游标推进都由服务端完成，"
    "模型本轮只负责「讲解上一题的对错 + 呈现当前这一道题」。\n"
    "因此**只出现一道题是正确的**，不要因为「用户说要考 5 道题、这里只有第 1 题」"
    "就判成内容遗漏——其余题目会在后续轮次逐一出现，不该也不能在本轮一次性给出。\n"
    "本轮该看的是：判分讲解有没有说反、该告知的「已存入错题集」有没有漏、"
    "呈现的题目是否与服务端给定的一致（不得篡改题干或选项）。\n"
    "用户本轮的原始请求如下：\n")


def _one_step_plan(goal: str, answer: str) -> tuple[Plan, dict]:
    """把简单直答的一问一答包成单步计划 + 产出，好喂给按多步设计的 Critic.review。

    review 只拿 plan 渲染一份「[id] description：summary」清单（见 critic._review_user），
    单步同样成立——无需为此另造一套评审接口。
    """
    step = PlanStep(id="1", description=goal, expected="直接答复用户", status="done")
    return Plan(goal=goal, steps=[step]), {"1": Artifact(summary=answer)}


def _redo_message(message: str, feedback: str) -> str:
    """重答指令：带上 Critic 的具体意见，并说明无需再调工具。

    考试轮所需的一切（判定结论、正确答案、解析、下一题题面）都在上下文的
    「[考试系统判定]…」提示里，重答只是重新组织文字。
    """
    return (f"{message}\n\n[结果校验] 你上一版答复未通过校验：{feedback or '未说明原因'}。"
            f"请针对该问题重新完整作答。所需信息上文均已给出，据此重新组织即可，"
            f"本次无需调用任何工具。")

SYNTH_SYSTEM = (
    "你是汇总员。根据用户目标和各步骤的产出，写出面向用户的最终答复。"
    "只使用已给出的产出，不要编造；条理清晰、直接作答。"
    # 子步被要求「信息不足先问不要猜」、critic 也已豁免这类产出；若汇总时把问题揉进正文
    # 或用假设填上，前两道的努力就白费了——用户根本看不到自己该回答什么。
    "若某步指出缺少必要信息、需要用户确认，最终答复必须把该问题**明确提给用户**："
    "先给出已经能给的部分，再清楚地列出还需要用户确认什么，不要略过，也不要自行假设填补。"
)


def _synth_user(goal: str, artifacts: dict[str, Artifact], recent_dialogue: str = "") -> str:
    lines = []
    if recent_dialogue:   # 带上最近对话，最终答复才有多轮上下文（指代/追问/延续先前话题）
        lines.append(f"最近对话（供理解上下文与延续语气）：\n{recent_dialogue}\n")
    lines += [f"用户目标：\n{goal}\n", "各步骤产出："]
    for sid, art in artifacts.items():
        # 与 _build_prompt 同理：id 不作前缀紧贴正文，否则最终答复会漏出 [s1] 残留
        lines.append(f"—— 步骤 {sid} 的产出 ——\n{art.summary}\n—— 以上为 {sid} ——")
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
                 max_step_retry: int = 2, max_replan: int = 2, skill_matcher=None) -> None:
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
        # 技能路由器：按触发词把用户消息确定性匹配到技能，命中剧本注入 planner/直答（None=不启用）
        self._skill_matcher = skill_matcher

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
    async def _is_simple(self, message: str, recent_dialogue: str = "") -> bool:
        """本轮该不该短路成简单直答。

        必须带上最近对话：只看孤立的一句，多步任务的追问（「再详细点」「继续」）会被判成
        简单，于是跳过规划退化成一轮 ReAct——该拆的步骤没拆，前端也从「计划树」变成
        「计划块 + 独立工具块」，同一个会话里忽合忽分。
        """
        user = message
        if recent_dialogue:
            tail = recent_dialogue[-_TRIAGE_DIALOGUE_MAX:]   # 保尾：越近的轮次越能说明当前意图
            user = f"【最近对话】\n{tail}\n\n【本次消息】\n{message}"
        try:
            raw = await self._fast_complete(TRIAGE_SYSTEM, user)
            return raw.strip().lower().startswith("simple")
        except Exception:
            return False   # 判不了就走完整编排（宁可多做不可少做）

    async def _simple_answer(self, message: str, budget=None, *, context=None, registry=None,
                             prefer_main: bool = False, skill_hint: str = ""):
        """简单问答短路：单个全能力 AgentLoop 直答，透传其事件（跳过其 RunStarted，避免重复）。

        承载绝大多数流量（问答/追问/考试）。context 为本轮每请求上下文（系统提示+全部指引+会话
        历史+记忆，由 chat 路由传入）——多轮对话、附件/考试/引用/日期/个性化全靠它；缺省回退到最小
        SYNTH_SYSTEM+澄清指引（测试/back-compat）。registry 为本轮每请求工具表（含用户级工具）。

        prefer_main：用主 client/model 而非快速档。用于有状态交互（考试）——这类流程指令繁杂，
        需可靠地按系统注入的「[考试系统判定]…请呈现下一题」提示逐题推进；快速档小模型常漏掉
        「呈现下一题」这一步（编排器化之前考试本就跑在主模型上，属回归修复）。且主档窗口更大，
        无需按快速档再收窗口——否则携带下一题文本的最后一条消息可能被 Clamp 截断而丢题。"""
        if skill_hint:   # 路由命中的技能剧本：作为参考前缀注入（方向3：主动挂载，不等模型 load_skill）
            message = (
                "【可参考的技能流程】以下是处理这类请求的推荐步骤，请据此完成本次请求"
                f"（仍以用户实际需求为准）：\n\n{skill_hint}\n\n---\n用户请求：{message}")
        ctx = context if context is not None else ContextManager(
            with_today(SYNTH_SYSTEM + CLARIFY_GUIDE))
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

    async def _simple_answer_verified(self, message: str, budget=None, *, context=None,
                                      registry=None, skill_hint: str = "",
                                      in_exam: bool = True):
        """带终局校验的简单直答。用于考试轮（force_simple + 前端结果校验开）。

        与多步路径的区别是**不重规划**：考试是有状态流程，重新拆解会打乱逐题推进。
        校验不过就地重答一次，且重答给空工具表——考试轮的原料（判定结论、正确答案、
        解析、下一题题面）已由服务端在「[考试系统判定]…」提示里给全，模型只需重新
        组织文字；留着工具反而可能让它再调一次 start_exam，把考试进度整个重置。

        第一版的 RunFinished 必须压住不发：它是终结信号，先发出去前端立刻标「已完成」，
        而这一版随时可能被重答顶掉（与交付门 passthrough=False 的处理同理）。
        """
        draft, finished_ev = "", None
        async for ev in self._simple_answer(message, budget, context=context,
                                            registry=registry, prefer_main=True,
                                            skill_hint=skill_hint):
            if isinstance(ev, TextDelta):
                draft += ev.text
            if isinstance(ev, RunFinished):
                finished_ev = ev          # 压住，等校验有结论再决定发不发
                continue
            yield ev

        if not draft.strip():             # 空产出：没什么可校验的，原样收尾
            if finished_ev is not None:
                yield finished_ev
            return

        yield Progress(scope="verify", text="结果校验中…", status="running")
        plan, arts = _one_step_plan(message, draft)
        # 校验器只看到「目标：抽取 5 道题考试」和「产出：第 1 题」，于是判「缺失第 2~5 题、
        # 实质性内容遗漏」——而逐题呈现恰恰是对的。误判的代价不对称：它会触发整轮重答，
        # 用户白等一次，重答出来的还是同一道题。故把考试的推进规则明确告诉校验器。
        review = await self._critic.review(
            (_EXAM_REVIEW_NOTE + message) if in_exam else message, plan, arts)
        if review.accept:
            yield Progress(scope="verify", text="结果校验通过", status="ok")
            if finished_ev is not None:
                yield finished_ev
            return

        yield Progress(scope="verify",
                       text=review.feedback or "存在缺口，重答一次", status="error")
        # 清屏：第一版已逐字流给用户，重答前清空，让新版从头打字机输出。
        # chat 路由据此同步清掉落库缓冲，否则最终落库的是「被否那版 + 新版」的拼接。
        yield Progress(scope="reset", text="")
        async for ev in self._simple_answer(_redo_message(message, review.feedback),
                                            budget, context=context,
                                            registry=ToolRegistry(), prefer_main=True):
            yield ev

    # ---- synthesize ----
    async def _synthesize(self, goal: str, artifacts: dict[str, Artifact], recent_dialogue: str = ""):
        """流式汇总最终答复。yield TextDelta（run() 累加得最终文本）+ ReasoningDelta（开思考模式时
        把最终答复的思考过程透传给前端——编排器路径唯一该展示思考的地方）。

        保持纯生成（空工具表、单步）以免在汇总阶段又去调工具；带上 recent_dialogue 让最终答复有
        多轮上下文。"""
        loop = AgentLoop(
            client=self._client, registry=ToolRegistry(),
            context=ContextManager(with_today(SYNTH_SYSTEM)), max_steps=1,
            model_name=self._model)
        final = ""
        streamed = False
        async for ev in loop.run(_synth_user(goal, artifacts, recent_dialogue)):
            if isinstance(ev, TextDelta):
                streamed = True
                yield ev
            elif isinstance(ev, ReasoningDelta):   # 思考过程透传（前端 ThinkingBlock 展示）
                yield ev
            elif isinstance(ev, ModelUsage):       # 用量记进累加器，run() 末尾汇总
                record_usage(ev.usage, ev.cost_usd, ev.model,
                             ev.latency_ms, ev.attempts)
            elif isinstance(ev, RunFinished):
                final = ev.message.content or ""
        # 端点未流式（只在 RunFinished 给全量）时，补一个 TextDelta，保证 run() 能累加到文本
        if final and not streamed:
            yield TextDelta(text=final)

    # ---- 主入口 ----
    async def run(self, user_message: str, verify: bool = True, *,
                  context=None, registry=None, recent_dialogue: str = "",
                  force_simple: bool = False, in_stateful_exam: bool = False,
                  run_id: str | None = None, purge_side_effects=None):
        """verify：对应前端结果校验开关。开 → 终局 Critic 把关 + 可重规划；关 → 跑完一轮
        直接汇总交付，不做终局 review/重规划（更快，但不把关）。

        force_simple：强制走简单直答（ReAct 单循环），跳过 triage 与 plan-execute-synthesize。
        用于「有状态、多轮、模型驱动」的交互流程——典型是模拟考试：模型调 start_exam 拿到题、
        同一轮原样呈现、下一轮由服务端 grade_exam_turn 拦截判分。这类流程只适合单循环：若被拆成
        多步再汇总，start_exam 的原样呈现指令会被执行子步/终局汇总两层概括吞掉，且 Critic 判某步
        不合格触发重试会再次 start_exam、把考试进度重置。chat 路由在命中考试语境（注入 EXAM_GUIDE）
        时置真。

        in_stateful_exam：是否**正在**逐题作答（考试会话进行中，或近期确实调过考试工具）。
        比 force_simple 窄一档，只用来关技能路由。二者必须分开：force_simple 宽是对的
        （「考我10道题」这类开考请求也得走单循环），但技能剧本只在真正逐题推进时才会
        干扰；若跟着 force_simple 一起关，「讲讲我的错题」只因含「错题」二字就被判成考试
        语境，错题精讲技能反被自己的触发词挡在门外。

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
            # 技能路由（方向2+3）：按触发词匹配命中的技能剧本，注入直答（参考）与 planner（拆解蓝本）。
            # 正在逐题作答时不叠加（in_stateful_exam）——技能剧本会干扰逐题推进。刻意不用
            # force_simple 把关：那个宽一档（含「考我10道题」这类尚未开考的请求），跟着它一起
            # 关会让「讲讲我的错题」因含「错题」二字就丢掉错题精讲技能。命中的剧本对简单直答
            # 同样有效（_simple_answer 收 skill_hint 作参考前缀），故这里放行不影响单循环。
            skill_hint = ""
            _matcher = getattr(self, "_skill_matcher", None)   # __new__ 构造的测试实例可能未设该属性
            if _matcher is not None and not in_stateful_exam:
                _matched = _matcher.match(user_message)
                if _matched is not None:
                    skill_hint = _matched.body
                    # 命中即发 skill 进度事件：前端「技能」块据此展示（与 load_skill 同 scope，复用渲染）。
                    # detail 带技能名（不带正文）：前端据此点开时去 /api/skills/{name} 取 md 正文——
                    # 正文是静态资源，随事件下发会在每条命中技能的消息里各存一份 2-3KB。
                    # 从 text 里反解技能名太脆弱（文案一改就断），故走 detail 这条稳定通道。
                    yield Progress("skill", f"已启用技能「{_matched.name}」：{_matched.description}",
                                   status="ok", detail={"skill": _matched.name})


            # 执行子步的工具视图。恒隐藏 update_plan（子步调它会覆盖总计划）。
            # save_to_knowledge 则按需隐藏：计划步骤文本再干净，子步也可能自作主张把整理好的
            # 笔记塞进知识库（实测「归纳成结构化学习笔记」这一步就直接存了）——那是用户自己
            # 策展的资料库，没要求就写入等于替他做主。validate_plan 只看计划文本，拦不到这种
            # 执行期的自作主张，故在工具层面直接不给。
            # 用户要求过、或命中的技能剧本本就以入库为目的（如「资料入库」）时不隐藏。
            _hidden = {"update_plan"}
            if not (_KB_REQUESTED_RE.search(user_message or "")
                    or "save_to_knowledge" in (skill_hint or "")):
                _hidden.add("save_to_knowledge")
            exec_reg = HidingRegistry(registry, _hidden) if registry is not None else None

            # 命中技能即走完整规划：技能剧本本身就是一套多步流程，该交给 planner 拆成计划步，
            # 而不是塞进单循环当「参考」——后者既不出计划步，模型还可能自己发一份没有 id 的
            # ReAct 清单，把工具块吞掉且展不开明细。
            # force_simple（考试等有状态单循环）永远优先，写在 or 左边先短路。
            # 注意它下面 skill_hint **可能非空**：技能路由的关闭条件是 in_stateful_exam（正在逐题
            # 作答），比 force_simple 窄——「讲讲我的错题」含考试触发词故 force_simple 为真，却仍
            # 该命中错题精讲技能。这类轮次走单循环 + 剧本作参考前缀（_simple_answer 收 skill_hint），
            # 既不拆成多步打乱逐题推进，也不丢技能。
            # 顺带省掉一次 triage 调用：命中技能时结论已定，不必再问模型。
            if force_simple or (not skill_hint
                                and (_obvious_simple(user_message)
                                     or await self._is_simple(user_message, recent_dialogue))):
                # 分流结论先于执行发出：这条路不出「任务步骤」块，用户此前只能靠「没有块」
                # 反推走了单循环，triage 误判（该拆步却判了 simple）也就无从察觉。
                yield Progress(scope="route", text="简单直答", key="route",
                               detail={"mode": "simple"}, status="ok")
                # 考试轮（force_simple）且开了结果校验 → 补一道终局校验 + 就地重答。
                # 判分/错题入库/游标推进都是服务端确定性完成的，模型只负责讲解与呈现下一题；
                # 讲解讲错（判定说反、漏告知「已存入错题集」、篡改下一题）此前无人兜底。
                # 其余简单轮维持原样：校验寒暄没有意义，且每轮多一次主模型往返会显著拖慢
                # 最快的那条路径。
                # 开了结果校验就校验，寒暄除外。此前只有考试轮（force_simple）才校验，理由是
                # 「校验寒暄没意义、且拖慢最快那条路径」——但简单路径如今也承接实质任务
                # （翻译/总结/改写这一段：原料已在消息里，不需拆步骤，triage 判 simple），
                # 那些是真交付物，开了开关却完全不校验，等于开关在这条路上形同虚设。
                # 仍放过 _obvious_simple（纯寒暄/致谢）：校验「你好」纯属白烧一次往返。
                if verify and (force_simple or not _obvious_simple(user_message)):
                    async for ev in self._simple_answer_verified(
                            user_message, budget, context=context, registry=registry,
                            skill_hint=skill_hint, in_exam=force_simple):
                        yield ev
                else:
                    async for ev in self._simple_answer(user_message, budget, context=context,
                                                        registry=registry,
                                                        prefer_main=force_simple,
                                                        skill_hint=skill_hint):
                        yield ev
                return

            # 与简单路径成对：在规划开始前发，让徽章与「任务计划思考」同时出现，而不是
            # 等计划出来才追认。后面规划失败降级到 _simple_answer 时不改口——那轮确实
            # 走了编排器，只是没成功，改成「简单直答」反而掩盖了失败。
            yield Progress(scope="route", text="多步规划", key="route",
                           detail={"mode": "plan"}, status="ok")

            # 规划器必须看到执行子步真正拿得到的那份工具视图（exec_reg，非裸 registry）：
            # 否则它会凭常识编出系统做不到的步骤（如「保存到 Notion/Obsidian」），执行子步
            # 读到这种描述就只会答「请手动保存」，不去调 save_to_knowledge。
            tools_desc = render_tool_roster(exec_reg if exec_reg is not None else registry)
            out: dict = {}   # 边规划边流式发规划思考（顶部"任务计划思考"块），先于计划
            async for ev in self._plan_streaming(
                    self._planner.plan(user_message, recent_dialogue, skill_hint,
                                       tools_desc=tools_desc), out):
                yield ev
            if "error" in out:
                async for ev in self._simple_answer(user_message, budget,   # 降级
                                                    context=context, registry=registry,
                                                    skill_hint=skill_hint):
                    yield ev
                return
            plan = out["plan"]
            yield _plan_progress(plan)

            replan_count = 0
            _review_history: list[dict] = []   # 每轮终局校验结论（供 verify 列结构化统计）
            _last_review_ok = True             # verify=False 时不校验，按通过记
            retry_hints: dict[str, str] = {}
            # 跨轮累积 done 产物：replan 返回全新 Plan（旧 done 步不在其中），必须在换 plan 前收走，
            # 否则终局 synthesize/review 只剩最后一轮的产物 —— 违反 spec §4「保留成果」并使闭环残废。
            all_artifacts: dict[str, Artifact] = {}
            # 本轮出现过「结构性无法完成」（Critic 判 impossible）的步：缺的工具/权限/能力
            # 在本系统根本不存在，重规划也变不出来——和单步重试一样徒劳。据此跳过重规划，
            # 直接带现有成果收尾。run 级累积：一旦有过就不再重规划。
            impossible_seen: set[str] = set()
            while True:
                async for ev in self._schedule_rounds(plan, retry_hints, budget, exec_reg,
                                                          goal=user_message,
                                                          purge_side_effects=purge_side_effects,
                                                          impossible_steps=impossible_seen):
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
                # 结构化留痕：Progress 里只有给人看的中文，统计侧解不出「过没过/拦在哪层」。
                # 交付门那套 verify 列此前只在已成死代码的交付门分支里写，编排器每轮都在
                # 校验、结论却全丢——统计页因此显示「从来没有回答被拦下过」。
                _review_history.append({"failed": [] if review.accept else ["review"],
                                        "feedback": review.feedback or ""})
                _last_review_ok = review.accept
                yield Progress(scope="verify",
                               text="结果校验通过" if review.accept
                                    else (review.feedback or "存在缺口，重新规划"),
                               status="ok" if review.accept else "error")
                if review.accept or replan_count >= self._max_replan:
                    break
                # 有步骤结构性无法完成 → 不重规划。缺的工具/权限/能力本系统根本没有，
                # 重新拆一版计划还是撞同一堵墙，只会空转 max_replan 轮。与单步不重试同理
                # （见 _on_step_fail 的 impossible），带现有成果如实收尾。review 仍已跑过一次，
                # 用于决定最终措辞；这里只拦住其后的重规划。
                if impossible_seen:
                    yield Progress(scope="verify",
                                   text="存在无法完成的步骤（缺必要工具/权限/能力），"
                                        "重新规划也无法解决，带现有成果收尾",
                                   status="error")
                    break
                # 命中技能 → 不重规划。技能剧本就是这类任务的既定流程，重新拆解等于把它推翻，
                # 用户会看到步骤中途凭空变样。重规划本是用来纠正「计划拆错了」的，而剧本恰恰
                # 规定了拆法；单步做砸由 max_step_retry 在原步骤内重试兜住，与此无关。
                # 故这里保持步骤不变，带现有产物去定稿。
                if skill_hint:
                    yield Progress(scope="verify",
                                   text="按技能既定流程执行，不重新拆解步骤",
                                   status="ok")
                    break
                replan_count += 1
                for s in plan.steps:
                    if s.status in ("pending", "running"):
                        s.status = "skipped"
                out2: dict = {}   # 重规划思考也流式发，在新计划之前
                async for ev in self._plan_streaming(
                        self._planner.replan(user_message, plan, review.feedback,
                                             skill_hint, tools_desc=tools_desc), out2):
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
            # 交付门结构化留痕：chat 路由据此写 conversation_messages.verify 列，
            # 统计页的「一次过率 / 降级交付 / 重答次数 / 哪一层拦下的」全靠它。
            # 走 Progress 的 detail 通道（已有的落库路径），不新增事件类型。
            # 只在真的跑了校验时发：verify=False 那轮一条 verify 事件都不该有——前端正是靠
            # 「本轮有无 verify 事件」决定交付前要不要盖住生成的文件，乱发会把文件藏起来。
            _degraded = any(st.status in ("failed", "skipped") for st in plan.steps)
            if verify:
                yield Progress(
                    scope="verify", text="", key=VERIFY_TRACE_KEY,
                    detail={"attempts": replan_count + 1, "retries": replan_count,
                            "ok": bool(_last_review_ok), "degraded": _degraded,
                            "gate_error": False, "history": _review_history})
            yield RunFinished(message=Message(role=Role.ASSISTANT, content=final))
        finally:
            reset_acc(acc_token)

    # ---- 调度：一轮轮跑就绪集，直到无 pending、预算超限或无法推进（后两者带现有成果收尾）----
    async def _schedule_rounds(self, plan: Plan, retry_hints: dict[str, str], budget=None,
                               exec_reg=None, goal: str = "", purge_side_effects=None,
                               impossible_steps: set | None = None):
        # 产出文件的步骤才拿得到 save_download。计划常拆成「1.生成内容 → 2.存成文件」，
        # 而工具表原先是按轮算的，两步都看得见它：第 1 步校验没过、被要求重试时就会抓它用上，
        # 第 2 步再存一次，下载区两份重复文件（实测症状）。此处按步收紧到该给的那步。
        # savers 为 None（没有一步像是要产文件）时不限制，退回原行为——正则漏判把该存的那步
        # 也堵死，比重复保存严重得多。
        savers = file_saving_step_ids(plan)
        # 每步「已经留下持久产物的工具」，跨重试累积。与 retry_hints 同生命周期：
        # 重试的 prompt 里此前只有质检意见，没有「上次已经做过什么」，于是带副作用的工具
        # 被原样重来——同一份笔记存两次即由此而来。
        step_effects: dict[str, list[str]] = {}

        def _reg_for(step: PlanStep):
            if exec_reg is None or savers is None or step.id in savers:
                return exec_reg
            return HidingRegistry(exec_reg, {"save_download"})

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
                terminal = False
                # 两份记录一起带：effects（工具名，告诉重跑的模型别重做）跨重试累积，
                # fx（产物 id，供作废时删除）每次尝试新起一份。
                # fx 传进去就地填：本步的异常若逃出 AgentLoop 的兜底（下面 except 分支），
                # StepArtifact 根本 yield 不出来，而那时文件可能已经落了盘——只搭在返回值上就丢了。
                effects = list(step_effects.get(step.id, ()))
                fx = empty_fx()
                try:
                    async for ev in self._executor.execute(
                            step, deps, retry_hints.get(step.id, ""),
                            registry=_reg_for(step), goal=goal,
                            done_effects=effects, fx_sink=fx):
                        if isinstance(ev, StepArtifact):
                            art, err, terminal = ev.artifact, ev.error, ev.terminal
                            effects = list(ev.effects)
                            # 真实 executor 里这与 fx_sink 是同一个对象，取哪个都一样。
                            # 判"有没有内容"而非用 or：empty_fx() 是含三个键的 dict、恒为真，
                            # 用 or 会让返回值无条件压过 sink——只填 sink、忘了设 side_effects
                            # 的实现，产物会被默认空值整个抹掉，既不清理也不摘工具名。
                            # 也不能 merge：两者常是同一对象，合并会把 id 记两遍。
                            if has_any(ev.side_effects):
                                fx = ev.side_effects
                        else:
                            await queue.put(("ev", ev))
                except Exception as e:  # 单步崩溃隔离
                    # 崩在工具调用之后也要记账：产物已经落库了，重试时照样不能重做
                    step_effects[step.id] = effects
                    await queue.put(("done", (step, None, str(e), False, fx)))
                    return
                step_effects[step.id] = effects
                await queue.put(("done", (step, art, err, terminal, fx)))

            tasks = [asyncio.create_task(_worker(s)) for s in ready]
            remaining = len(tasks)
            try:
                while remaining:
                    kind, payload = await queue.get()
                    if kind == "ev":
                        yield payload
                        continue
                    step, art, err, terminal, fx = payload
                    remaining -= 1
                    if art is None or err:
                        self._on_step_fail(step, retry_hints, err or "执行未产出结果",
                                           terminal=terminal)
                        for _ev in self._settle_failed_attempt(
                                step, fx, purge_side_effects, step_effects):
                            yield _ev
                        continue
                    verdict = await self._critic.validate(step, art)
                    if verdict.ok:
                        step.status = "done"
                        step.result = art
                        step.elapsed_ms = _elapsed(step)   # 定格耗时
                        retry_hints.pop(step.id, None)
                    else:
                        # 终态失败（不重试）的两种确定性情形，重跑都只会得到同样结果：
                        #   terminal —— 用户拒绝了本步里的危险操作（重跑=再怼一次弹窗）；
                        #   verdict.impossible —— 缺工具/权限/能力的结构性障碍（重跑=再说一遍做不到）。
                        # 其余的不通过是「没做好」，仍走重试兜底。
                        if verdict.impossible and impossible_steps is not None:
                            # 记给外层：这类失败重规划也解决不了，不该再拆一遍计划撞同一堵墙。
                            impossible_steps.add(step.id)
                        self._on_step_fail(step, retry_hints, verdict.reason,
                                           terminal=terminal or verdict.impossible)
                        for _ev in self._settle_failed_attempt(
                                step, fx, purge_side_effects, step_effects):
                            yield _ev
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

    @staticmethod
    def _settle_failed_attempt(step, fx, purge_side_effects, step_effects=None):
        """作废一次尝试：删掉它产出的用户可见产物，并让前端抖掉这一步的旧记录。

        为什么立即删而不像交付门那样延迟到交付时结算：单步重试的语义是"这次尝试整个作废"，
        产物当场失效，没有"等新版产出了同类东西再决定"的必要。延迟反而会让用户在重跑期间
        看到一个指向作废产物的下载按钮。

        清理与前端通知都不该影响主流程：purge 内部已吞掉 store 异常，这里也不因为
        没注入回调就中断——精简装配下本就可能没有下载/知识库能力。
        """
        # 顺序要紧：先把清理做完，再 yield。yield 是可被中断的点——客户端在这里断连，
        # GeneratorExit 抛出，后面的 purge 与摘工具名就都不执行了，产物成孤儿。
        # purge 是同步调用，提前做零成本。
        purged = None
        if purge_side_effects is not None and has_any(fx):
            purged = purge_side_effects(fx) or []
            if isinstance(purged, dict):
                # 删掉的产物要让模型重做：把对应工具名从该步的 done_effects 里摘掉。
                # 不摘的话重跑时模型仍被告知「你已经做过了」，于是不再保存——旧的删了、
                # 新的没生成，用户手里一个文件都不剩。这是两套机制的接缝，只有分组 dict
                # 才知道删的是哪一类，故只在这一支处理。
                if step_effects is not None:
                    redo = tools_to_redo(purged)
                    if redo:
                        step_effects[step.id] = [t for t in step_effects.get(step.id, ())
                                                 if t not in redo]
                purged = list(purged.get("download") or ())

        # 让前端抖掉这一步的旧记录，否则同一步里同一个工具会显示调了两遍。
        # **只在真会重跑时发**（_on_step_fail 已把状态置好：pending=重跑，failed=到此为止）。
        # 不重跑还抖掉记录，等于把失败现场一并抹了——这步调了什么、错在哪，用户再也看不到。
        if step.status == "pending":
            yield Progress("step_reset", step.id, status="ok")
        # 归一成扁平的下载 id 数组：前端拿到非数组会 Array.isArray 判假、直接 return——
        # 按钮永远撤不掉且不报错。
        if purged:
            yield Progress("purged", json.dumps(list(purged), ensure_ascii=False), status="ok")

    def _on_step_fail(self, step, retry_hints: dict[str, str], reason: str, *,
                      terminal: bool = False) -> None:
        step.attempts += 1
        if not terminal and step.attempts < self._max_step_retry:
            step.status = "pending"        # 重试：回到就绪集
            step.started_at_ms = None      # 清计时，重跑时重新起点
            retry_hints[step.id] = reason
        else:
            step.status = "failed"         # 放弃：依赖链自然断掉，交终局 Critic 判
            step.elapsed_ms = _elapsed(step)   # 定格耗时
            retry_hints.pop(step.id, None)

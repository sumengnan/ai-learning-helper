# app/orchestration/executor.py
"""Executor：执行单个计划步骤。

每步起一个独立上下文的 AgentLoop（通用 prompt + 全量工具），实现上下文隔离。
内部事件转 Progress（与 dispatch 一致），最终以 StepArtifact 信号带出结构化产物。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from harness.context.manager import ContextManager
from harness.events import (
    ModelUsage, Progress, RunError, RunFinished, StepStarted, ToolFinished, ToolStarted)
from harness.loop.agent_loop import AgentLoop
from harness.progress import reset_current_agent, set_current_agent
from harness.tools.base import ToolRegistry

from ..search_guidance import SEARCH_SYSTEM_GUIDANCE
from app.today import today_guide

from ..side_effects import empty_fx, ids_from_tool
from .plan import Artifact, PlanStep
from .usage_ctx import record_usage


class HidingRegistry(ToolRegistry):
    """对底层 registry 的**活视图**，隐藏若干工具名。底层后续新增的工具（如 startup 时才
    注册进 reg 的 MCP 远程工具）自动可见——故执行子步既拿得到 MCP 搜索工具、又看不到被隐藏的
    update_plan（子步调它会发 scope=plan 覆盖编排器总计划）。不能用静态拷贝：那会错过 startup
    才注册的工具。装配层与编排器（把每请求 registry 包成执行子步视图）共用。"""

    def __init__(self, base: ToolRegistry, hidden: set[str]) -> None:
        super().__init__()
        self._base = base
        self._hidden = set(hidden)

    def get(self, name: str):
        return None if name in self._hidden else self._base.get(name)

    def tools(self) -> list:
        return [t for t in self._base.tools() if t.name not in self._hidden]

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools()]

    def register(self, tool) -> None:
        self._base.register(tool)

    def unregister(self, name: str) -> None:
        self._base.unregister(name)


# 用户拒绝危险操作时工具回传的开头（见 harness/tools/builtins/shell_tool.py）。
# 据此把该步判成**终态失败**：重试只对偶发故障有意义，而拒绝是人做出的决定，
# 再跑一遍不会有不同结果，只会把同一个弹窗怼到用户脸上第二次、第三次。
_USER_DENIED_MARK = "命令未执行：用户拒绝了该操作"


@dataclass
class StepArtifact:
    """内部信号：Executor 产出的最终产物。Orchestrator 消费、不外发（非 Event）。

    不变式：error 非空时表示该步执行失败，此时 artifact.summary 可能为空字符串，
    消费方必须先查 error 再决定是否采信 summary。

    terminal=True 表示这次失败不可通过重试挽回（当前唯一来源：用户拒绝了危险操作），
    调度器应直接判 failed，不再重跑本步。

    两份产物记录，服务于两件不同的事，缺一不可：

    - effects：本次尝试里**已经留下持久产物**的工具名（去重、保序）。重试时要告诉模型
      「这些你上次已经做过了」，否则它会把带副作用的工具原样再调一遍——实测就是同一份
      笔记被存两次。判据见 execute() 里对 marker 的说明。
    - side_effects：同一批产物的 **id**（下载/知识/题目）。校验不过要重跑时，调度器据此
      把这一版的产物删掉。

    两者协作而非重复：删掉某类产物后，调度器会同步把对应的工具名从 effects 里摘掉，
    模型于是不会被告知「已做过」、会重新保存。只留 effects 则用户拿到的是不合格那版的
    文件；只留 side_effects 则删了却没人重做，用户手里一个文件都没有。
    """
    artifact: Artifact
    error: str | None = None
    terminal: bool = False
    effects: list[str] = field(default_factory=list)
    side_effects: dict = field(default_factory=empty_fx)


# 子步默认只有裸系统提示词，缺少主聊天那套工具引导，模型会拿 http_request/浏览器乱抓网页
# 而不调专门的联网搜索工具。这段引导补上工具偏好，让它优先用搜索工具。
EXECUTOR_GUIDE = (
    "\n\n【工具使用】需要最新、事实性或联网信息时，优先使用联网搜索工具"
    "（web 搜索类工具，如可用的 *_web_search），不要用 http_request 或浏览器逐个抓取网页——"
    "搜索工具更快、覆盖更全；http_request/浏览器只在需要读取某个具体已知网址时才用。"
)

# 信息不足先问、不要猜：主聊天路径（chat.py）与编排器（执行子步 + 简单直答）共用同一段文案，
# DRY。定义放在最低层的 executor 模块，供 chat.py / orchestrator.py 上行 import，避免循环依赖。
# 与 verify.py 立场一致（请求澄清/合理追问属恰当推进、不扣分）。
CLARIFY_GUIDE = (
    "\n\n【信息不足先问，不要猜】当完成任务缺少必需的关键信息（如目标、对象、范围、格式、版本、"
    "时间、约束等），且无法从已给的上下文/前置产出合理推断时，不要凭空假设或编造——"
    "宁可先向用户澄清确认，或在产出里明确标出「缺什么、需要用户确认什么」，也不要猜一个跑偏的结果。"
    "但也不要为无关紧要的细节反复纠结：信息已足够、或缺的只是不影响结果的小事时，"
    "按合理默认直接推进，并说明所采用的假设，让用户能纠正。")


def _system_with_guide(base: str, sandbox_guide_text: str = "") -> str:
    """给执行子步的系统提示词补上工具偏好引导 + 检索提问方式 + 信息不足先问 + 当前日期
    （时效/未来趋势类任务需知"现在"）。

    sandbox_guide_text 由装配层按配置预渲染（工作目录/镜像/联网，与主聊天路径共用 sandbox_guide，
    DRY），有沙箱时非空——让执行子步用对路径、并知道能否联网装包、该选哪个命令。"""
    # SEARCH_SYSTEM_GUIDANCE 紧跟 EXECUTOR_GUIDE：后者只说「优先用搜索工具」，没说 query 该怎么写，
    # 子步于是把用户原话整句照抄进 query。这段指引原本只拼在 harness.system_prompt 上（主聊天与
    # 简单直答经 context 拿得到），而执行子步的 base 是裸的 config.app_system_prompt——多步任务里
    # 联网检索恰恰归子步做，指引根本没到真正调工具的那个上下文。
    guide = (f"{base}{EXECUTOR_GUIDE}{SEARCH_SYSTEM_GUIDANCE}{CLARIFY_GUIDE}"
             + today_guide())
    return guide + (sandbox_guide_text or "")


# 带进子步的用户原始请求上限。够覆盖「翻译/总结/改写这一大段」这类把原料贴在消息里的
# 用法；再长的（整篇文档）本就该走附件或知识库，不宜每步都重复搬运。
_GOAL_MAX = 6000


# 重试时的既成事实告知。
#
# 措辞按「内容有没有变」分情况，不能一刀切说「不要再调用」：本步若**本职就是产出文件**
# （按步下发后，save_download 只会出现在这种步骤的 effects 里），质检不通过说的往往正是
# 产物内容不行；改好了却不许再存，下载区就永久留着被否的那一版，而 Critic 只看 summary
# 会判它通过——等于把一个 bug 换成另一个更隐蔽的。
#
# 内容确有改动时再存一次是**正确**的；内容没变时再存也无害（产物按内容判重，是空操作）。
# 真正要拦的是「为了保险起见把同一份东西换个名字再存一遍」——那正是重复文件的来源。
def _done_effects_note(effects: list[str]) -> str:
    return ("\n【上次尝试已经做过的事】本步上次运行时已经成功调用过："
            + "、".join(effects)
            + "。它们产生的产物**还在，没有丢失**。\n"
              "因此：产物内容若与上次一致，就**不要再调用一次**——那只会凭空多出一份重复的"
              "文件/记录；只有当你这次真的改动了产物内容时，才照常再调一次把新版本存进去。"
              "不要为了「保险起见」重复保存同一份东西。")


def _build_prompt(step: PlanStep, deps: dict[str, Artifact], hint: str = "",
                  goal: str = "", effects: list[str] | None = None) -> str:
    lines = []
    if goal:
        # 子步的上下文是全新的 ContextManager（只有系统提示词，无对话历史），execute() 此前
        # 也不收用户消息——于是子步对「用户到底说了什么」完全失明。
        # 计划步的 description 是对任务的**转述**，原料不在里面：用户发「翻译这段：<日志>」，
        # 规划器写出「将提供的英文文本翻译成中文」，子步拿到的就只有这句话，于是回
        # 「请提供文本」。终局校验连判三次未通过，判得没错——活确实没干成。
        # 描述自足的任务（如「搜索 AI 资讯」）不受影响，但原料贴在消息里的一大类必然失败。
        g = goal if len(goal) <= _GOAL_MAX else goal[:_GOAL_MAX] + "\n…（原文过长已截断）"
        lines.append(f"【用户的原始请求（可能含本步要处理的原文/数据，务必据此作答）】\n{g}\n")
    lines += [f"你的子任务：{step.description}", f"预期产出：{step.expected}"]
    if deps:
        # 措辞刻意强硬：上面的 EXECUTOR_GUIDE 在推「优先用联网搜索工具」，两者方向相反。
        # 原文只说「供参考」，压不过那股拉力——子步照样把前置结果晾在一边自己重搜一遍，
        # 既多一轮往返，产出也和前一步对不上。
        lines.append("\n前置步骤已经取得以下结果，**直接基于它们**完成本步：")
        for dep_id, art in deps.items():
            # 步骤 id 单独成行、与正文隔开：曾写成 f"[{dep_id}] {art.summary}"，前缀紧贴
            # 正文首行（如「[s2] # AI发展与应用总结」），模型复用这份内容时把「[s2] 」
            # 一起抄进产出，最终漏进用户下载的文件开头。
            lines.append(f"—— 步骤 {dep_id} 的产出 ——\n{art.summary}\n—— 以上为 {dep_id} ——")
        lines.append("以上结果即为本步的输入，默认已经够用。不要为「再确认一遍」重复检索；"
                     "只有当它们明显不足以完成本步时，才另行补充检索。")
    if hint:
        lines.append(f"\n上次尝试未通过质检，请改进：{hint}")
    if effects:
        lines.append(_done_effects_note(effects))
    # 节流引导：减少每步的联网/工具往返（延迟主要来自这些串行调用）
    lines.append("\n要高效：检索类工具最多调用 2-3 次，信息够了就直接作答，不必反复搜。")
    lines.append("完成后直接给出该子任务的结果。")
    return "\n".join(lines)


class Executor:
    def __init__(self, client, registry: ToolRegistry, system_prompt: str,
                 model: str, *, max_steps: int = 10, budget=None,
                 loop_detect_window: int = 0, disable_thinking: bool = False,
                 sandbox_guide_text: str = "", temperature: float | None = None) -> None:
        """temperature：执行子步的采样温度（None=不覆盖，用全局基准）。这是机械执行，
        工具入参不该飘；但也不设 0——带工具的循环温度过低更容易卡在重复调同一个工具上，
        正是 loop_detect_window 那套防打转逻辑在治的事。"""
        self._client = client
        self._registry = registry
        self._system_prompt = system_prompt
        self._model = model
        self._max_steps = max_steps
        self._budget = budget
        self._loop_detect_window = loop_detect_window
        # 装配层按配置预渲染的沙箱指引（工作目录/镜像/联网）；无沙箱为空、不提
        self._sandbox_guide_text = sandbox_guide_text
        # 子步是"带工具干活"的机械执行，思考链多为白烧延迟；开则本步强制关思考（与 fast/judge 档一致）
        self._disable_thinking = disable_thinking
        self._temperature = temperature

    async def execute(self, step: PlanStep, deps: dict[str, Artifact], hint: str = "",
                      *, registry: ToolRegistry | None = None, goal: str = "",
                      done_effects: list[str] | None = None,
                      fx_sink: dict | None = None):
        """执行一步。yield Progress 事件，最后 yield 一个 StepArtifact。

        registry：本轮每请求工具表（含用户级 save_download/知识库/考试/附件工具）。编排器传入
        （已隐藏 update_plan）；缺省回退装配期 registry（主要供测试）。

        done_effects：上次尝试里已经留下产物的工具名，拼进 prompt 告诉模型别重做。

        fx_sink：调用方给的产物 id 收集器，边跑边就地更新。StepArtifact 也会带同样的内容，
        但本步中途崩溃时它根本 yield 不出来——而那时文件可能已经落了盘，清单一丢就再没人
        去删它。故清单必须写进调用方持有的对象，而不是只搭在返回值上。"""
        prompt = _build_prompt(step, deps, hint, goal, done_effects)
        loop = AgentLoop(
            client=self._client, registry=registry if registry is not None else self._registry,
            context=ContextManager(
                _system_with_guide(self._system_prompt, self._sandbox_guide_text)),
            max_steps=self._max_steps, budget=self._budget, model_name=self._model,
            loop_detect_window=self._loop_detect_window)
        scope = f"subagent:executor:{step.id}"
        final_text = ""
        error = None
        user_denied = False
        # 本次尝试的产物 id：既写进调用方的 sink（崩溃也不丢），也随 StepArtifact 回传
        side_effects = fx_sink if fx_sink is not None else {}
        side_effects.update(empty_fx())
        tool_names: dict[str, str] = {}
        tool_args: dict[str, object] = {}   # 暂存入参，供完成行带全（前端按 key 合并只留最后一条）
        # 就是调用方传进来的那个 list（没传才新建）：executor 边跑边就地追加，于是子步中途
        # 崩溃时调用方手里也已经有账——产物那时已经落库了，重试仍不能当它没发生。
        # 只靠最后一个 StepArtifact 交账做不到这点：崩溃根本走不到那句 yield。
        # prompt 已在上面用这个 list 的初值拼好，后续追加不会回头影响它。
        effects: list[str] = done_effects if done_effects is not None else []
        token = set_current_agent(f"executor:{step.id}")
        # 子步采样温度：设在这里而非 AgentLoop 参数上，与 disable_thinking 同一形状——
        # contextvar 在 async for 驱动生成器时可见，finally 还原不影响外层那轮的意图温度。
        samp_token = None
        if self._temperature is not None:
            from harness.llm.sampling import set_sampling_override
            samp_token = set_sampling_override(temperature=self._temperature)
        think_token = None
        if self._disable_thinking:  # 本步强制关思考：叠加在外层 override 之上，finally 还原
            from harness.llm.openai_compat import (
                get_extra_body_override, set_extra_body_override)
            think_token = set_extra_body_override(
                {**get_extra_body_override(), "enable_thinking": False})
        try:
            # 步骤头行：让前端分组标题显示步骤描述而非裸 id（s1）
            yield Progress(scope, step.description, status="running", key=f"__hdr__:{step.id}")
            async for ev in loop.run(prompt):
                if isinstance(ev, ToolStarted):
                    tc = ev.tool_call
                    tool_names[tc.id] = tc.name
                    tool_args[tc.id] = tc.arguments
                    # 原始 ToolStarted/ToolFinished/StepStarted 一并透传（不止转成 Progress）：
                    # 它们经 sink 落 trajectory，供「AI 运行统计」聚合能力/步数——旧写法只发 Progress，
                    # 这些埋点在编排器复杂路径下全丢了。前端在有计划时不再另显扁平 steps（见 ChatView），
                    # 故不会与计划树里的执行明细重复。
                    yield ev
                    yield Progress(scope, f"调用工具 {tc.name}", status="running", key=tc.id,
                                   detail={"tool": tc.name, "args": tc.arguments})
                elif isinstance(ev, ToolFinished):
                    r = ev.result
                    name = tool_names.get(r.tool_call_id, "工具")
                    if r.is_error and _USER_DENIED_MARK in (r.content or ""):
                        user_denied = True          # 本步含被用户拒绝的操作 → 不可重试
                    # 登记产物 id：必须在这里而不是事后从 Progress 里扒——这里同时看得见
                    # 工具名、结果文本与 is_error，是唯一能准确判定"真产出了东西"的位置。
                    # 就地更新（不重新绑定）：调用方持有同一个 dict，崩溃时仍看得到已产出的东西
                    _new = ids_from_tool(name, r.content or "", r.is_error)
                    for _k, _v in _new.items():
                        side_effects[_k] += _v
                    # 「这次调用留下了持久产物」的判据：结果带了 marker（model_content 非空
                    # 即表示 content 里有只给机器看的尾巴，如〔下载ID:x〕〔知识ID:x〕）。
                    # 用 marker 而非硬编码工具名单：名单会漂，而 marker 是既有约定——
                    # 任何新工具只要按约定给产物发 id，就自动被这里覆盖，不用记得回来改。
                    # 失败的调用没有 marker：ToolExecutor 只在成功分支设 model_content，
                    # 故 is_error 那半边判断当前是冗余的（去掉行为不变，试过）。留着是因为
                    # 「失败的保存不该算数」是这里的实质要求，而它现在只由另一处实现保证——
                    # 那处一改，这里就是最后一道闸。
                    if not r.is_error and r.model_content is not None and name not in effects:
                        effects.append(name)
                    yield ev
                    yield Progress(scope, f"调用工具 {name}",
                                   status="error" if r.is_error else "ok", key=r.tool_call_id,
                                   detail={"tool": name, "args": tool_args.get(r.tool_call_id),
                                           "result": r.content, "is_error": r.is_error,
                                           "image": (r.meta or {}).get("image")})
                elif isinstance(ev, StepStarted):   # 透传：前端忽略，仅供 trajectory 统计步数
                    yield ev
                elif isinstance(ev, ModelUsage):   # 用量记进累加器，供 Orchestrator 末尾汇总
                    record_usage(ev.usage, ev.cost_usd, ev.model,
                                 ev.latency_ms, ev.attempts)
                elif isinstance(ev, RunFinished):
                    final_text = ev.message.content or ""
                elif isinstance(ev, RunError):
                    error = ev.error
        finally:
            reset_current_agent(token)
            if samp_token is not None:
                from harness.llm.sampling import reset_sampling_override
                reset_sampling_override(samp_token)
            if think_token is not None:
                from harness.llm.openai_compat import reset_extra_body_override
                reset_extra_body_override(think_token)
        # 拷一份出去：effects 可能就是调用方的 list，直接交出去会让两边共享可变状态。
        # 累进语义已由「就地追加到传入的 list」保证——本次没再调也不会丢掉上次的账。
        yield StepArtifact(Artifact(summary=final_text, data={}, files=[]),
                           error=error, terminal=user_denied, effects=list(effects),
                           side_effects=side_effects)

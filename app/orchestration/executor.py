# app/orchestration/executor.py
"""Executor：执行单个计划步骤。

每步起一个独立上下文的 AgentLoop（通用 prompt + 全量工具），实现上下文隔离。
内部事件转 Progress（与 dispatch 一致），最终以 StepArtifact 信号带出结构化产物。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from harness.context.manager import ContextManager
from harness.events import (
    ModelUsage, Progress, RunError, RunFinished, StepStarted, ToolFinished, ToolStarted)
from harness.loop.agent_loop import AgentLoop
from harness.progress import reset_current_agent, set_current_agent
from harness.tools.base import ToolRegistry

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
    """
    artifact: Artifact
    error: str | None = None
    terminal: bool = False


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
    """给执行子步的系统提示词补上工具偏好引导 + 信息不足先问 + 当前日期（时效/未来趋势类任务需知"现在"）。

    sandbox_guide_text 由装配层按配置预渲染（工作目录/镜像/联网，与主聊天路径共用 sandbox_guide，
    DRY），有沙箱时非空——让执行子步用对路径、并知道能否联网装包、该选哪个命令。"""
    guide = (f"{base}{EXECUTOR_GUIDE}{CLARIFY_GUIDE}"
             f"\n\n今日日期：{date.today().isoformat()}（涉及时效或未来趋势时以此为基准）。")
    return guide + (sandbox_guide_text or "")


def _build_prompt(step: PlanStep, deps: dict[str, Artifact], hint: str = "") -> str:
    lines = [f"你的子任务：{step.description}", f"预期产出：{step.expected}"]
    if deps:
        # 措辞刻意强硬：上面的 EXECUTOR_GUIDE 在推「优先用联网搜索工具」，两者方向相反。
        # 原文只说「供参考」，压不过那股拉力——子步照样把前置结果晾在一边自己重搜一遍，
        # 既多一轮往返，产出也和前一步对不上。
        lines.append("\n前置步骤已经取得以下结果，**直接基于它们**完成本步：")
        for dep_id, art in deps.items():
            lines.append(f"[{dep_id}] {art.summary}")
        lines.append("以上结果即为本步的输入，默认已经够用。不要为「再确认一遍」重复检索；"
                     "只有当它们明显不足以完成本步时，才另行补充检索。")
    if hint:
        lines.append(f"\n上次尝试未通过质检，请改进：{hint}")
    # 节流引导：减少每步的联网/工具往返（延迟主要来自这些串行调用）
    lines.append("\n要高效：检索类工具最多调用 2-3 次，信息够了就直接作答，不必反复搜。")
    lines.append("完成后直接给出该子任务的结果。")
    return "\n".join(lines)


class Executor:
    def __init__(self, client, registry: ToolRegistry, system_prompt: str,
                 model: str, *, max_steps: int = 10, budget=None,
                 loop_detect_window: int = 0, disable_thinking: bool = False,
                 sandbox_guide_text: str = "") -> None:
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

    async def execute(self, step: PlanStep, deps: dict[str, Artifact], hint: str = "",
                      *, registry: ToolRegistry | None = None):
        """执行一步。yield Progress 事件，最后 yield 一个 StepArtifact。

        registry：本轮每请求工具表（含用户级 save_download/知识库/考试/附件工具）。编排器传入
        （已隐藏 update_plan）；缺省回退装配期 registry（主要供测试）。"""
        prompt = _build_prompt(step, deps, hint)
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
        tool_names: dict[str, str] = {}
        tool_args: dict[str, object] = {}   # 暂存入参，供完成行带全（前端按 key 合并只留最后一条）
        token = set_current_agent(f"executor:{step.id}")
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
                    yield ev
                    yield Progress(scope, f"调用工具 {name}",
                                   status="error" if r.is_error else "ok", key=r.tool_call_id,
                                   detail={"tool": name, "args": tool_args.get(r.tool_call_id),
                                           "result": r.content, "is_error": r.is_error})
                elif isinstance(ev, StepStarted):   # 透传：前端忽略，仅供 trajectory 统计步数
                    yield ev
                elif isinstance(ev, ModelUsage):   # 用量记进累加器，供 Orchestrator 末尾汇总
                    record_usage(ev.usage, ev.cost_usd, ev.model)
                elif isinstance(ev, RunFinished):
                    final_text = ev.message.content or ""
                elif isinstance(ev, RunError):
                    error = ev.error
        finally:
            reset_current_agent(token)
            if think_token is not None:
                from harness.llm.openai_compat import reset_extra_body_override
                reset_extra_body_override(think_token)
        yield StepArtifact(Artifact(summary=final_text, data={}, files=[]),
                           error=error, terminal=user_denied)

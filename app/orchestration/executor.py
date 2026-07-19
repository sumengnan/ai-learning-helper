# app/orchestration/executor.py
"""Executor：执行单个计划步骤。

每步起一个独立上下文的 AgentLoop（通用 prompt + 全量工具），实现上下文隔离。
内部事件转 Progress（与 dispatch 一致），最终以 StepArtifact 信号带出结构化产物。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from harness.context.manager import ContextManager
from harness.events import ModelUsage, Progress, RunError, RunFinished, ToolFinished, ToolStarted
from harness.loop.agent_loop import AgentLoop
from harness.progress import reset_current_agent, set_current_agent
from harness.tools.base import ToolRegistry

from .plan import Artifact, PlanStep
from .usage_ctx import record_usage


@dataclass
class StepArtifact:
    """内部信号：Executor 产出的最终产物。Orchestrator 消费、不外发（非 Event）。

    不变式：error 非空时表示该步执行失败，此时 artifact.summary 可能为空字符串，
    消费方必须先查 error 再决定是否采信 summary。
    """
    artifact: Artifact
    error: str | None = None


# 子步默认只有裸系统提示词，缺少主聊天那套工具引导，模型会拿 http_request/浏览器乱抓网页
# 而不调专门的联网搜索工具。这段引导补上工具偏好，让它优先用搜索工具。
EXECUTOR_GUIDE = (
    "\n\n【工具使用】需要最新、事实性或联网信息时，优先使用联网搜索工具"
    "（web 搜索类工具，如可用的 *_web_search），不要用 http_request 或浏览器逐个抓取网页——"
    "搜索工具更快、覆盖更全；http_request/浏览器只在需要读取某个具体已知网址时才用。"
)


def _system_with_guide(base: str, sandbox_workspace: str | None = None) -> str:
    """给执行子步的系统提示词补上工具偏好引导 + 当前日期（时效/未来趋势类任务需要知道"现在"）。

    有沙箱时再补一段沙箱工作目录提醒（与主聊天路径共用 sandbox_guide，DRY），
    让执行子步用对的相对/绝对路径读写文件、放产物，而不是臆想宿主机目录。"""
    guide = (f"{base}{EXECUTOR_GUIDE}"
             f"\n\n今日日期：{date.today().isoformat()}（涉及时效或未来趋势时以此为基准）。")
    if sandbox_workspace:
        from app.sandbox_manager import sandbox_guide
        guide += sandbox_guide(sandbox_workspace)
    return guide


def _build_prompt(step: PlanStep, deps: dict[str, Artifact], hint: str = "") -> str:
    lines = [f"你的子任务：{step.description}", f"预期产出：{step.expected}"]
    if deps:
        lines.append("\n已知前置步骤的产出（供参考，不要重复其工作）：")
        for dep_id, art in deps.items():
            lines.append(f"[{dep_id}] {art.summary}")
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
                 sandbox_workspace: str | None = None) -> None:
        self._client = client
        self._registry = registry
        self._system_prompt = system_prompt
        self._model = model
        self._max_steps = max_steps
        self._budget = budget
        self._loop_detect_window = loop_detect_window
        # 有沙箱时把工作目录路径带上，供 _system_with_guide 提醒模型；无沙箱为 None、不提
        self._sandbox_workspace = sandbox_workspace
        # 子步是"带工具干活"的机械执行，思考链多为白烧延迟；开则本步强制关思考（与 fast/judge 档一致）
        self._disable_thinking = disable_thinking

    async def execute(self, step: PlanStep, deps: dict[str, Artifact], hint: str = ""):
        """执行一步。yield Progress 事件，最后 yield 一个 StepArtifact。"""
        prompt = _build_prompt(step, deps, hint)
        loop = AgentLoop(
            client=self._client, registry=self._registry,
            context=ContextManager(
                _system_with_guide(self._system_prompt, self._sandbox_workspace)),
            max_steps=self._max_steps, budget=self._budget, model_name=self._model,
            loop_detect_window=self._loop_detect_window)
        scope = f"subagent:executor:{step.id}"
        final_text = ""
        error = None
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
                    yield Progress(scope, f"调用工具 {tc.name}", status="running", key=tc.id,
                                   detail={"tool": tc.name, "args": tc.arguments})
                elif isinstance(ev, ToolFinished):
                    r = ev.result
                    name = tool_names.get(r.tool_call_id, "工具")
                    yield Progress(scope, f"调用工具 {name}",
                                   status="error" if r.is_error else "ok", key=r.tool_call_id,
                                   detail={"tool": name, "args": tool_args.get(r.tool_call_id),
                                           "result": r.content, "is_error": r.is_error})
                elif isinstance(ev, ModelUsage):   # 用量记进累加器，供 Orchestrator 末尾汇总
                    record_usage(ev.usage, ev.cost_usd)
                elif isinstance(ev, RunFinished):
                    final_text = ev.message.content or ""
                elif isinstance(ev, RunError):
                    error = ev.error
        finally:
            reset_current_agent(token)
            if think_token is not None:
                from harness.llm.openai_compat import reset_extra_body_override
                reset_extra_body_override(think_token)
        yield StepArtifact(Artifact(summary=final_text, data={}, files=[]), error=error)

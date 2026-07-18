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

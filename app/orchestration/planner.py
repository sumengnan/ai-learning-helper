# app/orchestration/planner.py
"""Planner：把用户目标拆成 DAG 计划。结构化 JSON 输出 + 落地即校验 + 有界重试。"""
from __future__ import annotations

from pydantic import BaseModel, ValidationError

from app.verify import call_json

from .plan import Plan, PlanStep, validate_plan


class PlannerError(Exception):
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
        for _ in range(self._max_retries + 1):
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

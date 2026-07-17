# app/orchestration/plan.py
"""编排器数据结构与纯 DAG 函数。零 LLM 调用、零 IO，可完全确定性单测。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

StepStatus = Literal["pending", "running", "done", "failed", "skipped"]
_TERMINAL_OK = ("done",)


@dataclass
class Artifact:
    """结构化步骤产出。summary 必填（喂下游/汇总）；data/files 为结构化扩展位。"""
    summary: str
    data: dict = field(default_factory=dict)
    files: list[str] = field(default_factory=list)


@dataclass
class PlanStep:
    id: str
    description: str
    expected: str
    depends_on: list[str] = field(default_factory=list)
    status: StepStatus = "pending"
    result: Artifact | None = None
    attempts: int = 0


@dataclass
class Plan:
    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    version: int = 1


@dataclass
class Verdict:
    ok: bool
    reason: str


@dataclass
class Review:
    accept: bool
    feedback: str


def validate_plan(steps: list[PlanStep]) -> str | None:
    """校验 DAG 合法性。返回人读错误串；合法返回 None。

    三条硬约束（守住则 Scheduler 永不死锁于非法结构）：id 唯一、依赖存在、无环。
    """
    if not steps:
        return "计划为空，至少需要一个步骤"
    ids = [s.id for s in steps]
    if len(set(ids)) != len(ids):
        dup = [i for i in ids if ids.count(i) > 1]
        return f"步骤 id 重复：{sorted(set(dup))}"
    idset = set(ids)
    for s in steps:
        for d in s.depends_on:
            if d not in idset:
                return f"步骤 {s.id} 依赖不存在的步骤 {d}"
    # 拓扑排序检测环（Kahn）
    indeg = {s.id: len(s.depends_on) for s in steps}
    adj: dict[str, list[str]] = {s.id: [] for s in steps}
    for s in steps:
        for d in s.depends_on:
            adj[d].append(s.id)
    queue = [i for i, deg in indeg.items() if deg == 0]
    seen = 0
    while queue:
        cur = queue.pop()
        seen += 1
        for nxt in adj[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    if seen != len(steps):
        return "计划存在环（依赖成环），无法拓扑排序"
    return None


def ready_steps(plan: Plan) -> list[PlanStep]:
    """就绪集：自身 pending 且所有依赖已 done 的步骤。保持原始顺序。"""
    done = {s.id for s in plan.steps if s.status in _TERMINAL_OK}
    return [s for s in plan.steps
            if s.status == "pending" and all(d in done for d in s.depends_on)]


def has_pending(plan: Plan) -> bool:
    """是否仍有待处理步骤（pending 或 running）。"""
    return any(s.status in ("pending", "running") for s in plan.steps)

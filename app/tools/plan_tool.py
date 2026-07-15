from __future__ import annotations

import contextvars
import json
import time
from typing import Literal

from pydantic import BaseModel, Field

from harness.events import Progress
from harness.progress import emit
from harness.tools.base import Tool, ToolError

PlanStatus = Literal["pending", "running", "done", "failed"]

# 每步耗时的计时表。模型每次传的是全量清单，故耗时只能由后端跨调用比对快照得出。
# 工具实例是进程级共享的（assembly 只注册一次），状态必须按轮隔离 → 用 contextvar，
# 由 chat 路由在每次尝试进入 loop 前 set/reset（与 progress emitter 同一处）。
_clock: contextvars.ContextVar = contextvars.ContextVar("plan_clock", default=None)


def set_plan_clock():
    """为本轮开一份干净的步骤计时表；返回 token 供 reset。"""
    return _clock.set({})


def reset_plan_clock(token) -> None:
    _clock.reset(token)


def _apply_timing(steps: list[dict]) -> list[dict]:
    """给每步补 elapsed_ms（原地）。

    running 首次出现即记起点，转 done/failed 时定格耗时、此后不再变。按 title 索引而非
    下标：失败重试会在中间插入新步骤，下标会整体错位。模型跳过 running 直接置 done 的步骤
    没有起点，不编耗时、留空。耗时随 plan JSON 一起 emit，而 progress 是落库的，
    所以刷新后依然在。
    """
    clock = _clock.get()
    if clock is None:            # 未设置（纯 harness 用法）→ 不计时，行为同旧版
        return steps
    now = time.monotonic()
    for s in steps:
        st = clock.setdefault(s["title"], {"start": None, "elapsed_ms": None})
        if s["status"] == "running" and st["start"] is None:
            st["start"] = now
        elif (s["status"] in ("done", "failed")
              and st["elapsed_ms"] is None and st["start"] is not None):
            st["elapsed_ms"] = int((now - st["start"]) * 1000)
        if st["elapsed_ms"] is not None:
            s["elapsed_ms"] = st["elapsed_ms"]
    return steps


class PlanStep(BaseModel):
    title: str = Field(min_length=1)
    status: PlanStatus = "pending"


PLAN_SYSTEM_GUIDANCE = (
    "\n\n## 任务步骤清单\n"
    "面对需要多步才能完成的任务时，第一步先调用 update_plan 工具，列出 3-6 个高层子任务"
    "（status 全为 pending）。之后每完成或失败一步，就再次调用 update_plan、传入完整的最新"
    "清单：已完成的置 done、正在做的置 running。若某步失败并需重试，把该步置 failed 并在其后"
    "新增一条重试步骤（running），继续执行。简单问答（打招呼、寒暄、单句事实问答）不要调用"
    "update_plan。已在清单里的步骤不要在正文里再逐条复述。"
)


class UpdatePlanTool(Tool):
    name = "update_plan"
    description = (
        "维护当前复杂任务的有序步骤清单并展示给用户。面对多步任务先调用它列出步骤；"
        "每完成/失败一步就再次调用、传入完整最新清单。简单问答不要调用。"
        "某步失败时把它标记为 failed 并在其后新增一条重试步骤。"
    )

    class Params(BaseModel):
        steps: list[PlanStep]

    async def run(self, params: "UpdatePlanTool.Params") -> str:
        if not params.steps:
            raise ToolError("steps 不能为空")
        steps = _apply_timing([{"title": s.title, "status": s.status} for s in params.steps])
        emit(Progress(scope="plan", text=json.dumps(steps, ensure_ascii=False), key="plan"))
        n = len(steps)
        failed = sum(1 for s in steps if s["status"] == "failed")
        return f"计划已更新（{n} 步{f'，{failed} 失败' if failed else ''}）"

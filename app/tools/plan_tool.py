from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from harness.events import Progress
from harness.progress import emit
from harness.tools.base import Tool, ToolError

PlanStatus = Literal["pending", "running", "done", "failed"]


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
        steps = [{"title": s.title, "status": s.status} for s in params.steps]
        emit(Progress(scope="plan", text=json.dumps(steps, ensure_ascii=False), key="plan"))
        n = len(steps)
        failed = sum(1 for s in steps if s["status"] == "failed")
        return f"计划已更新（{n} 步{f'，{failed} 失败' if failed else ''}）"

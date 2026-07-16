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
    "update_plan。已在清单里的步骤不要在正文里再逐条复述。\n"
    # 实测约 1/6 的多步任务在这里掉链子：清单停在「第3步 running、第4步 pending」，
    # 而第4步其实干完了（文件都生成了）。用户看到的就是一份永远差两步的清单。
    "【收尾要求】给出最终答复之前，必须最后再调用一次 update_plan，把每一步都置为终态"
    "（done 或 failed）——清单里不允许留下 pending/running。哪怕只剩最后一步刚做完，也要补这一次调用："
    "你不补，用户看到的就是一份停在半路、与事实不符的清单。"
)


_TERMINAL = ("done", "failed")


def unfinished_steps(plan_text: str) -> list[dict]:
    """从清单快照里挑出还没收尾（pending/running）的步骤。纯解析，零模型调用。

    这是本模块唯一 100% 可靠的判定：清单是模型自述，「它说没说完」是文本事实，
    服务端一眼可辨。而「那步到底做没做」只有模型知道——工具轨迹里 save_download
    对应哪一条是语义匹配，机械推不出来。故这里只做检测，修正必须回去问模型。
    """
    try:
        steps = json.loads(plan_text or "")
    except (TypeError, ValueError):
        return []
    if not isinstance(steps, list):
        return []
    return [s for s in steps
            if isinstance(s, dict) and s.get("status") not in _TERMINAL]


FINALIZE_SYSTEM = (
    "你是任务清单收尾员。给你一份任务步骤清单，以及本轮实际执行的工具摘要。"
    "请依据工具摘要中的**事实**，把每一步判定为 done（确实完成了）或 failed（没做或没做成）。"
    "证据不足以证明某步完成时，判 failed，不要臆测——宁可少报也不要虚报。"
    "步骤的 title 必须原样保留、顺序不变、条数不变。"
    "只输出 JSON 数组：[{\"title\":\"原标题\",\"status\":\"done\"或\"failed\"}]，不要多余文字。"
)


def finalize_user_prompt(plan_text: str, step_summary: str) -> str:
    return (f"任务清单：\n{plan_text}\n\n本轮实际执行的工具摘要：\n{step_summary or '（无）'}\n\n"
            f"请据实收尾这份清单。")


def merge_finalized(plan_text: str, finalized: list) -> list[dict] | None:
    """把模型给的收尾结果并回原清单：只允许改 status，title/顺序/条数一律以原清单为准。

    模型改写标题或增删步骤会让用户看到一份「不是自己那份」的清单，比不收尾更糟；
    故这里按下标对齐，只取 status，且只接受终态——其余一律驳回（返回 None，保持原样）。
    """
    try:
        orig = json.loads(plan_text or "")
    except (TypeError, ValueError):
        return None
    if not isinstance(orig, list) or not isinstance(finalized, list):
        return None
    if len(finalized) != len(orig):
        return None                       # 条数对不上 → 不可信，宁可不改
    out = []
    for o, f in zip(orig, finalized):
        st = f.get("status") if isinstance(f, dict) else None
        if o.get("status") in _TERMINAL:
            out.append(dict(o))           # 已终态的不许被改写
        elif st in _TERMINAL:
            out.append({**o, "status": st})
        else:
            return None                   # 该收尾的没给终态 → 整份驳回
    return out


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

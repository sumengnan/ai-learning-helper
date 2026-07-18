# app/orchestration/critic.py
"""Critic：分层反思。validate 单步校验、review 终局把关。

线上路径：判官调用抖动一律 fail-open（放行），对齐 app/verify.py「绝不因基建抖动拦交付」。
"""
from __future__ import annotations

import logging

from app.verify import call_json

from .plan import Artifact, Plan, PlanStep, Review, Verdict

_log = logging.getLogger(__name__)

VALIDATE_SYSTEM = (
    "你是单步质检员。给你一个子任务的描述、预期产出、以及实际产出。"
    "判断实际产出是否达成了预期产出。宽松务实：只要方向对、内容基本可用即算通过；"
    "只有明显答非所问、空洞、或与预期南辕北辙才判不通过。"
    '只输出 JSON：{"ok": true/false, "reason": "一句话理由"}。'
)

REVIEW_SYSTEM = (
    "你是终局质检员。给你用户目标和各步骤的产出。判断整体是否足以作为对用户的答复。"
    "若基本达成目标即通过（accept=true）；若有实质缺口（遗漏关键部分、明显错误）则不通过，"
    "并在 feedback 里说清缺什么，供重新规划参考。"
    '只输出 JSON：{"accept": true/false, "feedback": "不通过时说明缺口，通过可留空"}。'
)


def _coerce_bool(value, default: bool) -> bool:
    """把 LLM 返回的 ok/accept 字段稳健地转成 bool。

    json_object 只保证是合法 JSON、不保证字段类型；模型可能给字符串 "false"。
    裸 bool("false") == True 会把"不通过"误判成"通过"，故这里显式识别字符串真值。
    无法识别或缺失 → 回退 default（本模块用 True，保持 fail-open 方向）。
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("false", "no", "否", "0", ""):
            return False
        if s in ("true", "yes", "是", "1"):
            return True
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _validate_user(step: PlanStep, artifact: Artifact) -> str:
    return (f"子任务：{step.description}\n预期产出：{step.expected}\n\n"
            f"实际产出：\n{artifact.summary}\n\n请判定是否达成预期。")


def _review_user(goal: str, plan: Plan, artifacts: dict) -> str:
    lines = [f"用户目标：\n{goal}\n", "各步骤产出："]
    for s in plan.steps:
        art = artifacts.get(s.id)
        mark = art.summary if art else f"（未完成，状态={s.status}）"
        lines.append(f"- [{s.id}] {s.description}：{mark}")
    lines.append("\n请判定整体是否足以答复用户。")
    return "\n".join(lines)


class Critic:
    def __init__(self, complete, *, validate_complete=None) -> None:
        self._complete = complete                        # 终局 review 用（质量要求高，走主模型）
        self._validate = validate_complete or complete   # 单步 validate 用（频繁，可走快速档提速）

    async def validate(self, step: PlanStep, artifact: Artifact) -> Verdict:
        try:
            v = await call_json(self._validate, VALIDATE_SYSTEM, _validate_user(step, artifact))
            return Verdict(ok=_coerce_bool(v.get("ok"), True), reason=str(v.get("reason", "")))
        except Exception as e:  # fail-open：抖动放行
            _log.warning("Critic.validate 调用失败，fail-open 放行：%s", e)
            return Verdict(ok=True, reason=f"校验调用失败，放行：{str(e)[:120]}")

    async def review(self, goal: str, plan: Plan, artifacts: dict) -> Review:
        try:
            v = await call_json(self._complete, REVIEW_SYSTEM, _review_user(goal, plan, artifacts))
            return Review(accept=_coerce_bool(v.get("accept"), True), feedback=str(v.get("feedback", "")))
        except Exception as e:  # fail-open：抖动放行
            _log.warning("Critic.review 调用失败，fail-open 放行：%s", e)
            return Review(accept=True, feedback=f"审查调用失败，放行：{str(e)[:120]}")

# app/orchestration/critic.py
"""Critic：分层反思。validate 单步校验、review 终局把关。

线上路径：判官调用抖动一律 fail-open（放行），对齐 app/verify.py「绝不因基建抖动拦交付」。
"""
from __future__ import annotations

import logging

from app.verify import call_json

from .plan import Artifact, Plan, PlanStep, Review, Verdict

_log = logging.getLogger(__name__)

                                             # 与 app/verify.py 的澄清立场保持一致（见其
# JUDGE 提示词）：执行子步被明确要求「信息不足先问、不要猜」（executor.CLARIFY_GUIDE），
# 若这里再把「提问」判成未达成，就是一边让它问、一边因为它问而罚它——重试压力下模型只会
# 改去瞎猜，恰恰是那条指引要避免的结果。故澄清必须显式豁免，而不能只写在注释里。
_CLARIFY_EXEMPTION = (
    "【澄清豁免】若实际产出并非敷衍，而是**确实缺少无法自行推断的关键信息**"
    "（如目标对象、范围、格式、版本、时间、约束等），因而向用户提出具体问题、"
    "或明确标出「缺什么、需要用户确认什么」，这属于恰当推进，判为通过，不得因"
    "「没有给出最终产物」而判不通过。"
    "但要区分敷衍：泛泛地说「信息不足」却说不出缺哪一项，或缺的只是无关紧要、"
    "可按合理默认推进的细节，仍判不通过。"
)

# impossible 与 clarify 豁免的分界：能靠「问用户」解决的缺信息，走 clarify 判 ok=true
# （系统会把问题交付给用户）；而 impossible 是**问也没用**的结构性障碍——所需工具/权限/
# 能力在本系统里根本不存在（如「调用某内部系统」而工具表里没有、「访问需要登录的站点」而
# 无凭据）。这类步重试只是把同一句「我做不到」再说一遍，纯浪费，故直接终态放弃。
# 判据要严：只有产出**明确点出**缺的是哪项系统性能力、且非重试能补时才算；单纯没写好、
# 方向偏、内容浅，一律走普通 ok=false 让它重试——把「没做好」误判成「做不到」会把该救回
# 的步直接判死。
_IMPOSSIBLE_RULE = (
    "【无法完成】另有一种不通过：产出明确表明该步存在**结构性障碍**——完成它所必需的工具、"
    "权限或数据在本系统里根本不具备（例如需要调用某个并不存在的工具、访问需登录而无凭据的系统），"
    "且这不是重试或补充信息能解决的。此时置 impossible=true（同时 ok=false）：再试一遍只会"
    "得到同样的「做不到」，应终态放弃、交由整体收尾如实向用户说明。"
    "务必与上面的澄清豁免区分：缺的信息若能由用户提供，那是澄清（ok=true），不是无法完成。"
    "也务必与「只是没做好」区分：方向偏、内容浅、遗漏细节都仍是普通 ok=false，要留给重试。"
)

VALIDATE_SYSTEM = (
    "你是单步质检员。给你一个子任务的描述、预期产出、以及实际产出。"
    "判断实际产出是否达成了预期产出。宽松务实：只要方向对、内容基本可用即算通过；"
    "只有明显答非所问、空洞、或与预期南辕北辙才判不通过。"
    + _CLARIFY_EXEMPTION + _IMPOSSIBLE_RULE +
    '只输出 JSON：{"ok": true/false, "impossible": true/false, "reason": "一句话理由"}。'
)

REVIEW_SYSTEM = (
    "你是终局质检员。给你用户目标和各步骤的产出。判断整体是否足以作为对用户的答复。"
    "若基本达成目标即通过（accept=true）；若有实质缺口（遗漏关键部分、明显错误）则不通过，"
    "并在 feedback 里说清缺什么，供重新规划参考。"
    + _CLARIFY_EXEMPTION +
    "特别地，当缺口只能由用户回答时（需要用户提供信息或做选择），一律 accept=true："
    "重新规划拿不到用户没给过的信息，只会空转几轮后被迫瞎猜；正确做法是把问题交付给用户。"
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
            ok = _coerce_bool(v.get("ok"), True)
            # impossible 只在「判不通过」时才有意义：通过的步无所谓可否重试。这样即便模型
            # 把 ok=true 和 impossible=true 一起返回（矛盾输出），也不会误终结一个通过的步。
            impossible = (not ok) and _coerce_bool(v.get("impossible"), False)
            return Verdict(ok=ok, reason=str(v.get("reason", "")), impossible=impossible)
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

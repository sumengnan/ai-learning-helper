# app/orchestration/planner.py
"""Planner：把用户目标拆成 DAG 计划。结构化 JSON 输出 + 落地即校验 + 有界重试。"""
from __future__ import annotations

import re

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
    "你是任务规划器。把用户目标拆成 2-10 个高层子任务，输出一个有向无环图（DAG）。\n"
    "每个子任务含：id（如 s1，全局唯一）、description（要做什么）、expected（应产出什么，"
    "供质检比对）、depends_on（依赖的子任务 id 列表，无依赖填 []）。\n"
    "能并行的子任务不要人为串联（depends_on 留空）；只有真正需要前一步产出时才建立依赖。\n"
    "【必须落在可用工具范围内】给出可用工具清单时，只能规划这些工具做得到的事，"
    "但 description 是给用户看的，要用自然语言说这一步做什么（如「联网搜索最新资料」），"
    "不要写工具名或函数标识符（如 save_to_knowledge、mcp__websearch__xxx）。"
    "绝不要臆想本系统没有的外部产品或服务（如 Notion、Obsidian、邮箱、日历、第三方网盘）——"
    "用户说「保存到知识库」指的就是清单里的知识库保存工具，不是外部软件。"
    "若某件事清单里没有工具能做到，就不要把它排成步骤。\n"
    '只输出一个 JSON 对象：{"steps":[{"id":...,"description":...,"expected":...,"depends_on":[...]}]}，'
    "不要多余文字。"
)

# 工具清单的裁剪上限：描述只取首句、并截断，避免几十个工具（含 MCP 远程工具）把规划
# 提示词撑爆——规划器只需要知道"有什么、大致能干什么"，细节由执行子步自己看完整 schema。
_ROSTER_DESC_MAX = 60

def render_tool_roster(registry) -> str:
    """把 registry 渲染成紧凑的工具清单，供规划器知悉自己在为什么样的工具集做计划。"""
    if registry is None:
        return ""
    lines = []
    for t in registry.tools():
        desc = (t.description or "").strip()
        head = desc.split("。")[0].split("\n")[0][:_ROSTER_DESC_MAX]
        lines.append(f"- {t.name}：{head}" if head else f"- {t.name}")
    return "\n".join(lines)


_ROSTER_NAME_RE = re.compile(r"^- ([A-Za-z_][A-Za-z0-9_]*)", re.M)


def roster_names(tools_desc: str) -> set[str]:
    """从渲染好的工具清单里取回工具名，供净化步骤描述用。"""
    return set(_ROSTER_NAME_RE.findall(tools_desc or ""))


def strip_tool_names(text: str, names: set[str]) -> str:
    """从步骤描述里抹掉工具标识符——description 会被前端「任务步骤」块直接渲染给用户。

    提示词已要求模型别写工具名，但这是一道兜底：模型时灵时不灵，而漏出去的
    `mcp__websearch__bailian_web_search` 对用户是纯噪声。除清单内的工具名外，
    含 `__` 的标识符一律视为工具名（MCP 远程工具随时增删，未必在本次清单里）。
    连同前面的「使用/调用/通过」一起去掉，避免留下「使用 搜索资讯」这种断句。
    """
    if not text:
        return text
    alts = [re.escape(n) for n in sorted(names, key=len, reverse=True)]
    alts.append(r"[A-Za-z_][A-Za-z0-9_]*__[A-Za-z0-9_]+")
    tool = f"(?:{'|'.join(alts)})"
    # 先删整段括注（如「保存到知识库（使用 save_to_knowledge）」），再删正文中的提及
    out = re.sub(rf"[（(][^）)]*{tool}[^）)]*[）)]", "", text)
    out = re.sub(rf"(?:使用|调用|通过|用)?\s*{tool}\s*(?:工具)?\s*", "", out)
    return re.sub(r"\s{2,}", " ", out).strip()


def _plan_user(goal: str, recent_dialogue: str = "", tools_desc: str = "",
               skill_hint: str = "") -> str:
    ctx = f"最近对话（供理解上下文相关的请求，如指代/追问）：\n{recent_dialogue}\n\n" if recent_dialogue else ""
    # 工具清单在技能剧本之前：剧本是「怎么做」的蓝本，清单是「能做什么」的边界，
    # 边界先立，剧本里若提到本系统没有的手段也不至于被照抄成步骤。
    tools = f"可用工具清单：\n{tools_desc}\n\n" if tools_desc else ""
    # 路由命中的技能剧本：作为拆解蓝本注入（方向2），让 planner 按其步骤确定子任务与顺序
    hint = (f"参考以下技能流程来拆解计划（据此确定子任务与顺序，仍要贴合用户目标）：\n{skill_hint}\n\n"
            if skill_hint else "")
    return f"{ctx}{tools}{hint}用户目标：\n{goal}\n\n请拆成 DAG 计划。"


def _replan_user(goal: str, done: list[PlanStep], feedback: str, tools_desc: str = "") -> str:
    done_txt = "\n".join(f"- [{s.id}] {s.description}（已完成）" for s in done) or "（无）"
    tools = f"可用工具清单：\n{tools_desc}\n\n" if tools_desc else ""
    return (f"{tools}用户目标：\n{goal}\n\n已完成的步骤：\n{done_txt}\n\n"
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


def _scrub(steps: list[PlanStep], tools_desc: str) -> list[PlanStep]:
    """净化步骤描述里的工具名。抹空了就保留原文——宁可漏一个工具名，不可给用户空步骤。"""
    names = roster_names(tools_desc)
    for s in steps:
        cleaned = strip_tool_names(s.description, names)
        if cleaned:
            s.description = cleaned
    return steps


class Planner:
    def __init__(self, complete, *, max_retries: int = 2) -> None:
        self._complete = complete
        self._max_retries = max_retries

    async def plan(self, goal: str, recent_dialogue: str = "", skill_hint: str = "", *,
                   tools_desc: str = "") -> Plan:
        # skill_hint 保持位置参数（dev 的技能路由按位置传），tools_desc 只收关键字
        steps = await self._generate(
            PLANNER_SYSTEM, _plan_user(goal, recent_dialogue, tools_desc, skill_hint))
        return Plan(goal=goal, steps=_scrub(steps, tools_desc), version=1)

    async def replan(self, goal: str, plan: Plan, feedback: str, *,
                     tools_desc: str = "") -> Plan:
        done = [s for s in plan.steps if s.status == "done"]
        steps = await self._generate(
            PLANNER_SYSTEM, _replan_user(goal, done, feedback, tools_desc))
        return Plan(goal=goal, steps=_scrub(steps, tools_desc), version=plan.version + 1)

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

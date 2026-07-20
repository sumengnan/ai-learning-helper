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
    "你是任务规划器。把用户目标拆成 2-10 个高层子任务，输出一个有向无环图（DAG）。\n"
    "每个子任务含：id（如 s1，全局唯一）、description（要做什么）、expected（应产出什么，"
    "供质检比对）、depends_on（依赖的子任务 id 列表，无依赖填 []）。\n"
    "能并行的子任务不要人为串联（depends_on 留空）；只有真正需要前一步产出时才建立依赖。\n"
    "【必须落在可用工具范围内】给出可用工具清单时，只能规划这些工具做得到的事，"
    "需要动用工具的步骤要在 description 里点名该用哪个工具（写工具名）。"
    "绝不要臆想本系统没有的外部产品或服务（如 Notion、Obsidian、邮箱、日历、第三方网盘）——"
    "用户说「保存到知识库」指的就是清单里的知识库保存工具，不是外部软件。"
    "若某件事清单里没有工具能做到，就不要把它排成步骤。\n"
    "【不要规划「问用户」的步骤】计划会一口气自主执行完，执行步骤**没有与用户对话的通道**，"
    "排一个「与用户沟通/询问需求/确认偏好/了解当前水平」的步骤，它既问不出来也等不到回答，"
    "只会拖垮依赖它的后续步，最后交出一句「信息不足，无法完成」——用户什么都没拿到。"
    "缺少非关键信息时，**按合理默认直接做**（例如默认入门水平、每天 1-2 小时），"
    "并在产出里写明所用假设、请用户按需调整；确有必须由用户拍板的关键信息，也应在最终答复里"
    "提出，而不是排成一个步骤。\n"
    "【只规划用户要的事】工具清单是能力边界，不是待办清单——有某个工具不等于该用它。"
    "尤其是**带持久副作用的动作**（保存文件、写入知识库、增删改用户数据），"
    "除非用户明确要求，否则一律不要排进计划：用户只要一份答复时，就把答复做好，"
    "不要顺带产出他没要的文件或数据。也要留意工具描述里「仅用于…」这类限制，别越界使用。\n"
    '只输出一个 JSON 对象：{"steps":[{"id":...,"description":...,"expected":...,"depends_on":[...]}]}，'
    "不要多余文字。"
)

# 工具清单的裁剪上限：描述取首句 + 约束句，避免几十个工具（含 MCP 远程工具）把规划提示词
# 撑爆——规划器只需要知道"有什么、什么时候不该用"，细节由执行子步自己看完整 schema。
_ROSTER_DESC_MAX = 60
# 约束句给足预算：这类句子常以「请改用 xxx」收尾，截断会把工具名砍半，指向一个不存在的
# 名字，比不写还糟。宁可多几十字。
_ROSTER_CONSTRAINT_MAX = 110
_ROSTER_MAX_CONSTRAINTS = 2

# 约束句的开头标志。只取首句会把护栏截掉：save_to_knowledge 的「仅用于用户明确要存进
# 知识库的场景」正是第二句，丢了它规划器就把工具清单当菜单，挨个安排进计划。
_CONSTRAINT_HEADS = ("仅", "只", "注意", "不要", "禁止", "务必", "若", "除非")

# 不进规划清单的「内部机制」工具：它们服务于 AI 自身的记忆/经验/技能装载，不是用户可见的
# 任务步骤。排进计划只会多一次往返，还把内部管道当成「任务进度」暴露给用户。执行子步仍握有
# 这些工具、需要时自行调用——这里只是不让它们成为被规划出来的步骤。
_PLANNER_HIDDEN = frozenset({
    "recall_episodes", "record_episode", "remember", "search_memory",
    "load_skill", "unload_skill", "read_skill_resource", "update_plan",
})


def _describe(desc: str) -> str:
    """取首句 + 至多两句约束：既让规划器知道能干什么，也知道什么时候不该用。"""
    sentences = [s.strip() for s in (desc or "").replace("\n", "").split("。") if s.strip()]
    if not sentences:
        return ""
    head = sentences[0][:_ROSTER_DESC_MAX]
    limits = [s[:_ROSTER_CONSTRAINT_MAX] for s in sentences[1:]
              if s.startswith(_CONSTRAINT_HEADS) or "请改用" in s][:_ROSTER_MAX_CONSTRAINTS]
    return f"{head}（{'；'.join(limits)}）" if limits else head


def render_tool_roster(registry) -> str:
    """把 registry 渲染成紧凑的工具清单，供规划器知悉自己在为什么样的工具集做计划。"""
    if registry is None:
        return ""
    lines = []
    for t in registry.tools():
        if t.name in _PLANNER_HIDDEN:
            continue
        body = _describe(t.description or "")
        lines.append(f"- {t.name}：{body}" if body else f"- {t.name}")
    return "\n".join(lines)


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


def _replan_user(goal: str, done: list[PlanStep], feedback: str, tools_desc: str = "",
                 skill_hint: str = "") -> str:
    done_txt = "\n".join(f"- [{s.id}] {s.description}（已完成）" for s in done) or "（无）"
    tools = f"可用工具清单：\n{tools_desc}\n\n" if tools_desc else ""
    # 重规划同样要带上技能剧本：漏了它，新计划会在「不知道有技能」的前提下重拆，
    # 步骤凭空变样。（编排器在命中技能时本就不走重规划，这里是防止其它调用路径漏传。）
    hint = (f"参考以下技能流程来拆解计划（据此确定子任务与顺序，仍要贴合用户目标）：\n{skill_hint}\n\n"
            if skill_hint else "")
    return (f"{tools}{hint}用户目标：\n{goal}\n\n已完成的步骤：\n{done_txt}\n\n"
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

    async def plan(self, goal: str, recent_dialogue: str = "", skill_hint: str = "", *,
                   tools_desc: str = "") -> Plan:
        # skill_hint 保持位置参数（dev 的技能路由按位置传），tools_desc 只收关键字
        steps = await self._generate(
            PLANNER_SYSTEM, _plan_user(goal, recent_dialogue, tools_desc, skill_hint), goal)
        return Plan(goal=goal, steps=steps, version=1)

    async def replan(self, goal: str, plan: Plan, feedback: str, skill_hint: str = "", *,
                     tools_desc: str = "") -> Plan:
        done = [s for s in plan.steps if s.status == "done"]
        steps = await self._generate(
            PLANNER_SYSTEM, _replan_user(goal, done, feedback, tools_desc, skill_hint), goal)
        return Plan(goal=goal, steps=steps, version=plan.version + 1)

    async def _generate(self, system: str, user: str, goal: str = "") -> list[PlanStep]:
        last_err = ""
        for _ in range(self._max_retries + 1):
            u = user if not last_err else f"{user}\n\n上次输出无效：{last_err}。请修正后重新输出。"
            try:
                raw = await call_json(self._complete, system, u)
                steps = _parse_steps(raw)
            except Exception as e:  # 解析/schema/网络任一失败 → 记错重试
                last_err = str(e)[:200]
                continue
            # 传 goal：「未经要求写入知识库」这条约束要看用户到底要没要求过
            err = validate_plan(steps, goal)
            if err is None:
                return steps
            last_err = err
        raise PlannerError(f"规划失败（重试 {self._max_retries} 次后）：{last_err}")

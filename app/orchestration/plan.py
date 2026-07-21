# app/orchestration/plan.py
"""编排器数据结构与纯 DAG 函数。零 LLM 调用、零 IO，可完全确定性单测。"""
from __future__ import annotations

import re
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
    started_at_ms: int | None = None   # 本步开跑时刻（epoch 毫秒），供前端进行中读秒
    elapsed_ms: int | None = None       # 本步耗时（毫秒，定格值），供前端已结束步显示


@dataclass
class Plan:
    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    version: int = 1


@dataclass
class Verdict:
    """单步校验结论（Reflect 的单步层）：ok 表示该步产出是否达成预期。"""
    ok: bool
    reason: str


@dataclass
class Review:
    """终局把关结论（Reflect 的整体层）：accept 表示整体是否可交付；不通过时 feedback 供重规划。"""
    accept: bool
    feedback: str


# 「问用户」类步骤的识别。计划在一轮内自主跑完，执行子步没有与用户对话的通道（工具表里
# 不存在任何提问工具），这种步骤既问不出来也等不到回答，只会输出「需要用户提供 X」，
# 拖垮依赖它的后续步，最终汇总成「信息不足，无法完成」——用户什么都没拿到。
# 模式刻意收窄到「交互动词 + 用户」的搭配：「为用户生成练习题」「整理用户上传的资料」
# 这类正当步骤必须不被误伤（见 tests 里的反向用例）。
_ASK_USER_RE = re.compile(
    r"(向|与|和|跟)用户(沟通|确认|询问|提问|核实)"
    r"|(询问|问|请教|咨询)用户"
    r"|(请|让|要求)用户(提供|确认|回答|补充|告知|说明)"
    r"|等待用户"
    r"|待用户(回复|确认|提供|反馈)"
    r"|收集用户(需求|信息|偏好)"
    r"|了解用户(的)?[^，。；]{0,6}(需求|目标|水平|偏好|情况)")


# 「未经要求就写入知识库」的识别。知识库是用户自己策展的资料库，往里塞东西会污染他的检索
# 结果、事后还得手动清理——这与 save_download「产出用户要的交付物」性质不同，后者本就是答案
# 的文件形态，故不在此拦。要求「保存动词 + 知识库」相邻搭配，避免误伤「检索知识库中的资料」
# 这类正当的读取步骤。
_SAVE_KB_RE = re.compile(
    r"(?:存|保存|写|录|加|放|沉淀|归档)入?[^，。；]{0,4}知识库"
    r"|知识库[^，。；]{0,6}(?:保存|存档|入库|收藏)"
    r"|save_to_knowledge")

# 「这一步的产出是文件」的识别，用于把 save_download 只发给该步（见 file_saving_step_ids）。
# 计划常拆成「1.生成内容 → 2.存成文件」，而工具表此前是按轮算的，两步都看得见 save_download。
# 于是第 1 步在校验没过、被要求重试时，会抓这个看起来能「把事做成」的工具用上，
# 第 2 步再存一次——下载区两份重复文件。光靠工具描述劝不住：重试语境下模型压力更大。
_SAVE_FILE_RE = re.compile(
    r"save_download"
    r"|(?:存|保存|写|生成|导出|输出|落|产出)(?:成|为|出|到)?[^，。；]{0,6}"
    r"(?:可下载|下载|文件|附件|成品)"
    r"|(?:可下载|下载)[^，。；]{0,4}(?:文件|成品|产物)"
    r"|\.(?:md|txt|html|csv|json)\b")


def file_saving_step_ids(plan) -> set[str] | None:
    """哪些步骤该拿到 save_download；返回 None 表示不限制（维持全员可见）。

    单调设计：只有当计划里**确实存在**至少一步看着像「产出文件」时才收紧，一步都没命中
    就退回现状。否则正则漏判会把该存的那步也堵死——那比重复保存严重得多（用户什么都拿不到）。
    """
    hits = {s.id for s in plan.steps
            if _SAVE_FILE_RE.search(f"{s.description} {s.expected}")}
    return hits or None


# 用户目标里出现这些词才算「要求过」，此时上面的步骤是正当的
_KB_REQUESTED_RE = re.compile(r"知识库|收藏|存起来|入库|存档|保存下来")

# 「引用前置产出却没连依赖」的识别。执行子步只有在 depends_on 非空时才会收到前置步骤的产出
# （executor._build_prompt 的 `if deps:`），否则手里空空。一个写着「根据搜索结果整合」的步骤
# 若依赖为空，子步拿不到任何搜索结果，就会自己再搜一遍——既浪费往返，也和前一步的结果对不上。
# 只匹配明确指向「前面已经拿到的东西」的措辞；「搜索最新资料」这类自身即检索的步骤不算。
_NEEDS_DEPS_RE = re.compile(
    r"根据(?:搜索|检索|收集|调研|上述|前述|前面|以上)"
    r"|基于(?:搜索|检索|收集|上一步|前置|前面|以上)"
    r"|(?:搜索|检索|收集|调研)(?:结果|到的(?:信息|资料|内容))"
    r"|(?:前置|前一步|上一步|前面步骤|各步)(?:的)?产出")


def validate_plan(steps: list[PlanStep], goal: str = "") -> str | None:
    """校验 DAG 合法性。返回人读错误串；合法返回 None。

    三条硬约束（守住则 Scheduler 永不死锁于非法结构）：id 唯一、依赖存在、无环。
    外加一条可执行性约束：不得规划「问用户」步骤（本函数的返回值会被 Planner._generate
    拼进下一次提示驱动重试，故这里的错误串要写成能指导模型改正的话）。
    """
    if not steps:
        return "计划为空，至少需要一个步骤"
    kb_asked = bool(_KB_REQUESTED_RE.search(goal or ""))
    for s in steps:
        if not kb_asked and _SAVE_KB_RE.search(f"{s.description} {s.expected}"):
            return (f"步骤 {s.id}「{s.description[:30]}」要把内容写入用户知识库，但用户并没有"
                    "要求这么做。知识库是用户自己整理的资料库，擅自写入会污染他的检索结果、"
                    "事后还得手动清理。请删掉这一步：用户只要一份答复或文件时，交付答复/文件即可。")
        if not s.depends_on and _NEEDS_DEPS_RE.search(f"{s.description} {s.expected}"):
            return (f"步骤 {s.id}「{s.description[:30]}」要用前面步骤的产出，但 depends_on 是空的。"
                    "执行时只有依赖里的步骤产出会被传给它，依赖为空它就什么也拿不到，"
                    "只能自己重新检索一遍——既浪费往返，结果也和前一步对不上。"
                    "请把它真正依赖的步骤 id 填进 depends_on。")
        if _ASK_USER_RE.search(f"{s.description} {s.expected}"):
            return (f"步骤 {s.id}「{s.description[:30]}」是在向用户提问，但计划会自主跑完、"
                    "没有与用户对话的通道，这一步永远得不到回答。请删掉它：缺少的非关键信息"
                    "按合理默认直接推进（在产出里写明所用假设），确有必须由用户拍板的关键"
                    "信息时，也应在最终答复里提出，而不是排成一个步骤。")
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

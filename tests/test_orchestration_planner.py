import json
import pytest

from app.orchestration.planner import (
    PLANNER_SYSTEM, Planner, PlannerError, render_tool_roster, roster_names, strip_tool_names)
from app.orchestration.plan import Plan


def _complete_returning(*payloads):
    """返回一个 async complete，依次吐出预设 JSON 文本（每次调用消费一个）。"""
    seq = list(payloads)
    calls = {"n": 0}

    async def complete(system, user):
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return seq[i]

    return complete, calls


def _complete_capturing(*payloads):
    """同 _complete_returning，但额外记录每次传入的 user prompt，供断言反馈注入。"""
    seq = list(payloads)
    calls = {"n": 0, "users": []}

    async def complete(system, user):
        calls["users"].append(user)
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return seq[i]

    return complete, calls


async def test_plan_ok():
    payload = json.dumps({"steps": [
        {"id": "s1", "description": "查资料", "expected": "找到要点", "depends_on": []},
        {"id": "s2", "description": "汇总", "expected": "一段总结", "depends_on": ["s1"]},
    ]})
    complete, _ = _complete_returning(payload)
    plan = await Planner(complete).plan("写一篇总结")
    assert isinstance(plan, Plan)
    assert [s.id for s in plan.steps] == ["s1", "s2"]
    assert plan.steps[1].depends_on == ["s1"]
    assert plan.goal == "写一篇总结"


async def test_plan_retries_on_invalid_then_succeeds():
    bad = json.dumps({"steps": [{"id": "s1", "description": "a", "expected": "b", "depends_on": ["sX"]}]})
    good = json.dumps({"steps": [{"id": "s1", "description": "a", "expected": "b", "depends_on": []}]})
    complete, calls = _complete_returning(bad, good)
    plan = await Planner(complete, max_retries=2).plan("g")
    assert [s.id for s in plan.steps] == ["s1"]
    assert calls["n"] == 2   # 第一次非法、第二次成功


async def test_plan_raises_after_exhausting_retries():
    bad = json.dumps({"steps": [{"id": "s1", "description": "a", "expected": "b", "depends_on": ["sX"]}]})
    complete, calls = _complete_returning(bad)
    with pytest.raises(PlannerError):
        await Planner(complete, max_retries=1).plan("g")
    assert calls["n"] == 2   # 首次 + 1 次重试


async def test_replan_keeps_version_and_goal():
    p1 = json.dumps({"steps": [{"id": "s1", "description": "a", "expected": "b", "depends_on": []}]})
    complete, _ = _complete_returning(p1)
    plan = await Planner(complete).plan("g")
    p2 = json.dumps({"steps": [{"id": "s2", "description": "c", "expected": "d", "depends_on": []}]})
    complete2, _ = _complete_returning(p2)
    plan2 = await Planner(complete2).replan("g", plan, "上一版漏了X")
    assert plan2.version == plan.version + 1
    assert plan2.goal == "g"


async def test_parse_schema_failure_retries_then_succeeds():
    # 缺 description/expected → pydantic schema 校验失败，走 except 分支重试
    bad = json.dumps({"steps": [{"id": "s1"}]})
    good = json.dumps({"steps": [{"id": "s1", "description": "a", "expected": "b", "depends_on": []}]})
    complete, calls = _complete_returning(bad, good)
    plan = await Planner(complete, max_retries=2).plan("g")
    assert [s.id for s in plan.steps] == ["s1"]
    assert calls["n"] == 2


async def test_retry_feedback_injected_into_next_prompt():
    # 验证：上次的错误原因确实被拼进了下一次调用的 user prompt
    bad = json.dumps({"steps": [{"id": "s1", "description": "a", "expected": "b", "depends_on": ["sX"]}]})
    good = json.dumps({"steps": [{"id": "s1", "description": "a", "expected": "b", "depends_on": []}]})
    complete, calls = _complete_capturing(bad, good)
    await Planner(complete, max_retries=2).plan("g")
    assert "上次输出无效" in calls["users"][1]   # 第二次调用带上了纠错反馈
    assert "上次输出无效" not in calls["users"][0]  # 第一次没有


async def test_replan_exhausts_and_raises():
    # replan 侧的重试/失败路径
    p1 = json.dumps({"steps": [{"id": "s1", "description": "a", "expected": "b", "depends_on": []}]})
    complete0, _ = _complete_returning(p1)
    plan = await Planner(complete0).plan("g")
    bad = json.dumps({"steps": [{"id": "s2", "description": "a", "expected": "b", "depends_on": ["sY"]}]})
    complete, calls = _complete_returning(bad)
    with pytest.raises(PlannerError):
        await Planner(complete, max_retries=1).replan("g", plan, "fb")
    assert calls["n"] == 2


# ---------- 工具清单注入：规划器必须知道自己在为什么样的工具集做计划 ----------

class _FakeTool:
    def __init__(self, name, description):
        self.name = name
        self.description = description


class _FakeRegistry:
    def __init__(self, tools):
        self._tools = tools

    def tools(self):
        return self._tools


def test_render_tool_roster_keeps_head_and_constraints():
    """首句给能力、约束句给边界。回归：只取首句会把「仅用于用户明确要求」这类护栏截掉，
    规划器便把工具清单当菜单，给「总结一下」这种请求顺带排上存知识库、存文件。"""
    reg = _FakeRegistry([
        _FakeTool("save_to_knowledge", "把内容存入用户知识库。"
                                       "仅用于用户明确要「存进知识库」的场景。"
                                       "注意：要成品文档请改用 save_download。"),
        _FakeTool("calculator", "四则运算"),
    ])
    roster = render_tool_roster(reg)
    assert "存入用户知识库" in roster                      # 首句：能干什么
    assert "仅用于用户明确要" in roster                     # 约束句：什么时候不该用
    assert "请改用 save_download" in roster                # 指向的工具名不能被截半
    assert "- calculator：四则运算" in roster               # 无约束句时不加括号


def test_render_tool_roster_hides_internal_machinery():
    """记忆/经验/技能装载是 AI 的内部机制，不该被规划成用户可见的任务步骤
    （执行子步仍握有这些工具，只是不由规划器排进计划）。"""
    reg = _FakeRegistry([
        _FakeTool("search_knowledge", "检索用户知识库。"),
        _FakeTool("recall_episodes", "检索过往相似任务的经验。"),
        _FakeTool("search_memory", "检索你自己记下的长期记忆。"),
        _FakeTool("remember", "写入长期记忆。"),
        _FakeTool("load_skill", "装载技能。"),
    ])
    names = [ln.split("：")[0][2:] for ln in render_tool_roster(reg).splitlines()]
    assert names == ["search_knowledge"]


def test_planner_system_forbids_unrequested_side_effects():
    """规划器必须被明确告知：有工具 != 该用它，带持久副作用的动作不能擅自排进计划。"""
    from app.orchestration.planner import PLANNER_SYSTEM
    assert "只规划用户要的事" in PLANNER_SYSTEM
    assert "持久副作用" in PLANNER_SYSTEM


def test_planner_system_does_not_upgrade_content_request_to_file():
    """「整理成笔记」不得被规划成文件产出。

    覆盖 Bug：「整理检索到的AI资料，归纳成结构化的学习笔记」这类只要内容的请求，
    却生成了下载文件。根因是【一个交付物只排一步】原本举例说 expected 该写成
    「可供下载的学习笔记文件」——它为修「两步各存一份、下载区重复文件」而加，却把
    「整理成笔记」默认成了要文件，与上一条【只规划用户要的事】（除非明确要求，
    不得排保存文件）直接打架，而且它更具体，赢了。

    expected 会原样进执行子步提示（executor._build_prompt 的「预期产出：…」），
    还是 Critic.validate 的质检基准——写成文件，子步就必须调 save_download 才能过质检。
    """
    from app.orchestration.planner import PLANNER_SYSTEM
    # 「整理成笔记」这类说法必须与「写进答复正文」绑定，而不是与文件绑定
    assert "写进答复正文" in PLANNER_SYSTEM
    assert "整理成笔记" in PLANNER_SYSTEM
    # 「可供下载」只能出现在「用户明说要文件」那一支里
    head, _, tail = PLANNER_SYSTEM.partition("可供下载")
    assert "明说" in head[-120:] or "导出" in head[-120:], (
        "「可供下载」必须紧跟在「用户明说要导出/存成文件」的条件之后，"
        "不能作为「整理成笔记」的默认交付形态")


def test_render_tool_roster_none_registry_is_empty():
    assert render_tool_roster(None) == ""


async def test_plan_prompt_carries_tool_roster():
    """回归：不给工具清单，规划器会编出系统没有的外部产品（如 Notion/Obsidian），
    执行子步读到那种描述就不会调 save_to_knowledge。"""
    payload = json.dumps({"steps": [
        {"id": "s1", "description": "用 save_to_knowledge 存入知识库",
         "expected": "已入库", "depends_on": []}]})
    complete, calls = _complete_capturing(payload)
    roster = render_tool_roster(_FakeRegistry([
        _FakeTool("save_to_knowledge", "把内容存入用户知识库。")]))
    await Planner(complete).plan("搜索最新 AI 资讯并保存到知识库", tools_desc=roster)
    prompt = calls["users"][0]
    assert "可用工具清单" in prompt
    assert "save_to_knowledge" in prompt


async def test_replan_prompt_carries_tool_roster():
    payload = json.dumps({"steps": [
        {"id": "s1", "description": "a", "expected": "b", "depends_on": []}]})
    complete, calls = _complete_capturing(payload)
    plan = Plan(goal="g", steps=[], version=1)
    await Planner(complete).replan("g", plan, "反馈", tools_desc="- save_to_knowledge：存知识库")
    assert "save_to_knowledge" in calls["users"][0]


# —— 步骤描述不得暴露工具名（前端「任务步骤」块直接渲染 description）——

def test_strip_tool_names_removes_mcp_identifier_with_verb():
    out = strip_tool_names("使用 mcp__websearch__bailian_web_search 搜索最新 AI 资讯",
                           {"mcp__websearch__bailian_web_search"})
    assert out == "搜索最新 AI 资讯"


def test_strip_tool_names_removes_parenthetical_mention():
    out = strip_tool_names("保存到知识库（使用 save_to_knowledge）", {"save_to_knowledge"})
    assert out == "保存到知识库"


def test_strip_tool_names_strips_unknown_mcp_style_names():
    """名字带 __ 的一律视为工具标识符，即使不在清单里（MCP 远程工具随时增删）。"""
    out = strip_tool_names("调用 mcp__foo__bar 抓取页面", set())
    assert out == "抓取页面"


def test_strip_tool_names_keeps_plain_text_untouched():
    text = "整理错题并归纳薄弱知识点"
    assert strip_tool_names(text, {"save_to_knowledge"}) == text


def test_strip_tool_names_does_not_eat_normal_english_words():
    text = "总结 AI 资讯要点"
    assert strip_tool_names(text, {"save_to_knowledge"}) == text


def test_roster_names_parses_tool_names():
    roster = render_tool_roster(_FakeRegistry([
        _FakeTool("save_to_knowledge", "把内容存入用户知识库。"),
        _FakeTool("calculator", "四则运算。")]))
    assert roster_names(roster) == {"save_to_knowledge", "calculator"}


async def test_plan_scrubs_tool_names_from_step_description():
    payload = json.dumps({"steps": [
        {"id": "s1", "description": "使用 save_to_knowledge 存入知识库",
         "expected": "已入库", "depends_on": []}]})
    complete, _ = _complete_capturing(payload)
    roster = render_tool_roster(_FakeRegistry([
        _FakeTool("save_to_knowledge", "把内容存入用户知识库。")]))
    plan = await Planner(complete).plan("保存到知识库", tools_desc=roster)
    assert plan.steps[0].description == "存入知识库"


async def test_replan_scrubs_tool_names_from_step_description():
    payload = json.dumps({"steps": [
        {"id": "s1", "description": "调用 mcp__websearch__bailian_web_search 搜资讯",
         "expected": "拿到资讯", "depends_on": []}]})
    complete, _ = _complete_capturing(payload)
    plan = Plan(goal="g", steps=[], version=1)
    out = await Planner(complete).replan("g", plan, "反馈", tools_desc="")
    assert out.steps[0].description == "搜资讯"


def test_planner_system_forbids_writing_tool_names():
    assert "不要写工具名" in PLANNER_SYSTEM


def test_planner_system_forbids_splitting_produce_and_save():
    """内容加工与保存文件不可拆成两步——拆了两步都会各存一份。"""
    assert "不要拆成两步" in PLANNER_SYSTEM

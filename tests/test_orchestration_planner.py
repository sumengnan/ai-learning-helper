import json
import pytest

from app.orchestration.planner import Planner, PlannerError, render_tool_roster
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

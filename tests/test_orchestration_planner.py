import json
import pytest

from app.orchestration.planner import Planner, PlannerError
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

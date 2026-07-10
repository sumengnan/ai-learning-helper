import json
import pytest
from pydantic import ValidationError

from harness import progress
from harness.events import Progress
from app.tools.plan_tool import UpdatePlanTool, PlanStep, PLAN_SYSTEM_GUIDANCE


async def test_update_plan_emits_ordered_snapshot():
    tool = UpdatePlanTool()
    got = []
    token = progress.set_emitter(got.append)
    try:
        r = await tool.run(tool.Params(steps=[
            PlanStep(title="查资料", status="running"),
            PlanStep(title="汇总", status="pending"),
        ]))
    finally:
        progress.reset_emitter(token)

    evs = [e for e in got if isinstance(e, Progress)]
    assert len(evs) == 1
    assert evs[0].scope == "plan" and evs[0].key == "plan"
    assert json.loads(evs[0].text) == [
        {"title": "查资料", "status": "running"},
        {"title": "汇总", "status": "pending"},
    ]
    assert "2 步" in r


async def test_update_plan_reports_failed_count():
    tool = UpdatePlanTool()
    got = []
    token = progress.set_emitter(got.append)
    try:
        r = await tool.run(tool.Params(steps=[
            PlanStep(title="算", status="failed"),
            PlanStep(title="重试：换沙箱", status="running"),
        ]))
    finally:
        progress.reset_emitter(token)
    assert "1 失败" in r


async def test_update_plan_empty_raises_and_emits_nothing():
    from harness.tools.base import ToolError
    tool = UpdatePlanTool()
    got = []
    token = progress.set_emitter(got.append)
    try:
        with pytest.raises(ToolError):
            await tool.run(tool.Params(steps=[]))
    finally:
        progress.reset_emitter(token)
    assert got == []


def test_update_plan_rejects_unknown_status():
    with pytest.raises(ValidationError):
        UpdatePlanTool.Params(steps=[{"title": "x", "status": "bogus"}])


def test_guidance_mentions_tool_and_gating():
    assert "update_plan" in PLAN_SYSTEM_GUIDANCE
    assert "简单问答" in PLAN_SYSTEM_GUIDANCE

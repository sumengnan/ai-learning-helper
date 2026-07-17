import json

from app.orchestration.critic import Critic
from app.orchestration.plan import Artifact, Plan, PlanStep


def _complete_json(payload_dict):
    async def complete(system, user):
        return json.dumps(payload_dict)
    return complete


def _raising_complete():
    async def complete(system, user):
        raise RuntimeError("端点抖动")
    return complete


def _step():
    return PlanStep(id="s1", description="查质数定义", expected="给出质数定义")


async def test_validate_ok():
    critic = Critic(_complete_json({"ok": True, "reason": "符合预期"}))
    v = await critic.validate(_step(), Artifact(summary="质数是只有1和自身两个因子的数"))
    assert v.ok is True


async def test_validate_fail():
    critic = Critic(_complete_json({"ok": False, "reason": "答非所问"}))
    v = await critic.validate(_step(), Artifact(summary="今天天气不错"))
    assert v.ok is False and "答非所问" in v.reason


async def test_validate_fail_open_on_error():
    """线上路径：判官调用抖动 → 放行（ok=True），绝不因基建抖动拦交付。"""
    critic = Critic(_raising_complete())
    v = await critic.validate(_step(), Artifact(summary="x"))
    assert v.ok is True and "放行" in v.reason


async def test_review_accept():
    critic = Critic(_complete_json({"accept": True, "feedback": ""}))
    plan = Plan(goal="g", steps=[_step()])
    r = await critic.review("g", plan, {"s1": Artifact(summary="ok")})
    assert r.accept is True


async def test_review_reject_with_feedback():
    critic = Critic(_complete_json({"accept": False, "feedback": "缺少示例"}))
    plan = Plan(goal="g", steps=[_step()])
    r = await critic.review("g", plan, {"s1": Artifact(summary="ok")})
    assert r.accept is False and "示例" in r.feedback


async def test_review_fail_open_on_error():
    critic = Critic(_raising_complete())
    plan = Plan(goal="g", steps=[_step()])
    r = await critic.review("g", plan, {"s1": Artifact(summary="ok")})
    assert r.accept is True and "放行" in r.feedback

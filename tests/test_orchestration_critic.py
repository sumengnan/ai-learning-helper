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


async def test_validate_coerces_string_false():
    # 模型把 ok 输出成字符串 "false" → 必须判不通过（不被 bool("false")==True 骗过）
    critic = Critic(_complete_json({"ok": "false", "reason": "不达标"}))
    v = await critic.validate(_step(), Artifact(summary="x"))
    assert v.ok is False


async def test_validate_missing_ok_field_fail_open():
    # 缺 ok 字段视作异常 → fail-open 放行
    critic = Critic(_complete_json({"reason": "无 ok 字段"}))
    v = await critic.validate(_step(), Artifact(summary="x"))
    assert v.ok is True


async def test_review_with_unfinished_step():
    # plan 里有步骤但 artifacts 缺其产出 → _review_user 走"未完成"分支，仍正常返回
    critic = Critic(_complete_json({"accept": False, "feedback": "s2 没做"}))
    plan = Plan(goal="g", steps=[_step(), PlanStep(id="s2", description="第二步", expected="产出2", status="failed")])
    r = await critic.review("g", plan, {"s1": Artifact(summary="ok")})  # 故意缺 s2
    assert r.accept is False

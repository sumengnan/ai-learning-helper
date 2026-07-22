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


async def test_validate_uses_separate_completer_review_uses_main():
    """validate 走 validate_complete（快速档），review 走主 complete；各自命中不串。"""
    calls = []
    def _tagged(tag, payload):
        async def complete(system, user):
            calls.append(tag)
            return json.dumps(payload)
        return complete
    critic = Critic(_tagged("main", {"accept": True, "feedback": ""}),
                    validate_complete=_tagged("fast", {"ok": True, "reason": "ok"}))
    await critic.validate(_step(), Artifact(summary="x"))
    assert calls == ["fast"], "validate 应走 validate_complete"
    await critic.review("目标", Plan(goal="g", steps=[_step()]), {"s1": Artifact(summary="x")})
    assert calls == ["fast", "main"], "review 应走主 complete"


async def test_validate_complete_defaults_to_main():
    """不传 validate_complete 时回退主 complete（向后兼容）。"""
    calls = []
    async def complete(system, user):
        calls.append("main"); return json.dumps({"ok": True, "reason": ""})
    critic = Critic(complete)
    await critic.validate(_step(), Artifact(summary="x"))
    assert calls == ["main"]


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


# ---------- 澄清豁免：让子步「信息不足先问」，就不能因为它问了而罚它 ----------

def test_validate_system_exempts_clarification():
    """executor.CLARIFY_GUIDE 要求子步信息不足先问不要猜；若 critic 再把提问判成未达成，
    就是一边让它问、一边因它问而罚它——重试压力下模型只会改去瞎猜。"""
    from app.orchestration.critic import VALIDATE_SYSTEM
    assert "澄清豁免" in VALIDATE_SYSTEM
    assert "判为通过" in VALIDATE_SYSTEM
    # 必须同时防敷衍，否则模型可以用「信息不足」万能过关
    assert "说不出缺哪一项" in VALIDATE_SYSTEM


def test_review_system_delivers_question_instead_of_replanning():
    """缺口只能由用户回答时必须放行：重规划拿不到用户没给过的信息，只会空转后瞎猜。"""
    from app.orchestration.critic import REVIEW_SYSTEM
    assert "澄清豁免" in REVIEW_SYSTEM
    assert "只能由用户回答" in REVIEW_SYSTEM
    assert "accept=true" in REVIEW_SYSTEM


def test_synth_system_surfaces_the_question_to_user():
    """汇总环节若把问题揉进正文或自行假设填补，前两道豁免就白做了。"""
    from app.orchestration.orchestrator import SYNTH_SYSTEM
    assert "明确提给用户" in SYNTH_SYSTEM
    assert "不要自行假设填补" in SYNTH_SYSTEM or "不要自行假设" in SYNTH_SYSTEM


async def test_validate_impossible_marks_terminal_signal():
    """结构性障碍（缺工具/权限/能力，问也没用）→ impossible=true，供编排器终态放弃、不白重试。"""
    critic = Critic(_complete_json(
        {"ok": False, "impossible": True, "reason": "需调用不存在的内部系统工具"}))
    v = await critic.validate(_step(), Artifact(summary="我无法完成：系统里没有该工具"))
    assert v.ok is False and v.impossible is True


async def test_impossible_only_meaningful_when_not_ok():
    """impossible 只在判不通过时有意义：模型若矛盾地同时给 ok=true+impossible=true，
    不能把一个通过的步误终结。"""
    critic = Critic(_complete_json({"ok": True, "impossible": True, "reason": "矛盾输出"}))
    v = await critic.validate(_step(), Artifact(summary="其实做好了"))
    assert v.ok is True and v.impossible is False


async def test_plain_fail_is_not_impossible():
    """只是没做好（方向偏/内容浅）默认不是 impossible——该留给重试，不能判死。"""
    critic = Critic(_complete_json({"ok": False, "reason": "内容太浅"}))
    v = await critic.validate(_step(), Artifact(summary="敷衍两句"))
    assert v.ok is False and v.impossible is False


async def test_validate_failopen_is_never_impossible():
    """校验器抖动 fail-open 放行时，绝不能顺手标 impossible——那会把「校验挂了」误判成
    「任务做不到」，反而把本可重试/交付的步终结掉。"""
    critic = Critic(_raising_complete())
    v = await critic.validate(_step(), Artifact(summary="x"))
    assert v.ok is True and v.impossible is False

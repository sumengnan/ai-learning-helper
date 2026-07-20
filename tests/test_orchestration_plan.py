import pytest

from app.orchestration.plan import (
    Artifact, PlanStep, Plan, validate_plan, ready_steps, has_pending,
)


def _step(id, deps=(), status="pending"):
    return PlanStep(id=id, description=f"做{id}", expected=f"产出{id}",
                    depends_on=list(deps), status=status)


def test_validate_plan_ok():
    steps = [_step("s1"), _step("s2", deps=["s1"])]
    assert validate_plan(steps) is None


def test_validate_plan_duplicate_id():
    steps = [_step("s1"), _step("s1")]
    assert "重复" in validate_plan(steps)


def test_validate_plan_dangling_dep():
    steps = [_step("s1", deps=["sX"])]
    assert "sX" in validate_plan(steps)


def test_validate_plan_cycle():
    steps = [_step("s1", deps=["s2"]), _step("s2", deps=["s1"])]
    assert "环" in validate_plan(steps)


def test_validate_plan_empty():
    assert "空" in validate_plan([])


def test_ready_steps_only_deps_satisfied():
    steps = [_step("s1", status="done"), _step("s2", deps=["s1"]), _step("s3", deps=["s1", "s2"])]
    plan = Plan(goal="g", steps=steps)
    ready = ready_steps(plan)
    assert [s.id for s in ready] == ["s2"]   # s3 还差 s2


def test_ready_steps_parallel_batch():
    steps = [_step("s1"), _step("s2"), _step("s3", deps=["s1"])]
    plan = Plan(goal="g", steps=steps)
    ready = ready_steps(plan)
    assert {s.id for s in ready} == {"s1", "s2"}   # 无依赖两步同批


def test_ready_steps_blocked_by_failed():
    steps = [_step("s1", status="failed"), _step("s2", deps=["s1"])]
    plan = Plan(goal="g", steps=steps)
    assert ready_steps(plan) == []   # 依赖失败 → 永不就绪


def test_has_pending():
    assert has_pending(Plan(goal="g", steps=[_step("s1")]))
    assert not has_pending(Plan(goal="g", steps=[_step("s1", status="done")]))


def test_artifact_defaults():
    a = Artifact(summary="x")
    assert a.data == {} and a.files == []


def test_validate_plan_self_loop():
    # 自环 s1→s1 也是环，必须报"环"
    assert "环" in validate_plan([_step("s1", deps=["s1"])])


def test_validate_plan_single_step():
    # 单步无依赖是合法计划
    assert validate_plan([_step("s1")]) is None


def test_has_pending_running_is_true():
    plan = Plan(goal="g", steps=[_step("s1", status="running")])
    assert has_pending(plan)


def test_has_pending_failed_and_skipped_are_false():
    # failed/skipped 是终态（非成功），不算 pending
    plan = Plan(goal="g", steps=[_step("s1", status="failed"), _step("s2", status="skipped")])
    assert not has_pending(plan)


# ---------- 不得规划「问用户」步骤 ----------

def test_rejects_ask_user_step():
    """回归：计划一口气自主跑完，执行子步没有与用户对话的通道（工具表里没有任何提问工具）。
    排「与用户沟通」这种步骤，它问不出来也等不到回答，只会拖垮后续步，最终交出
    「信息不足，无法生成」——用户什么都没拿到。"""
    steps = [PlanStep(id="s1", description="与用户沟通，了解学习目标、当前水平、每天可用时间",
                      expected="用户的目标与水平"),
             PlanStep(id="s2", description="拆解知识点", expected="知识点列表", depends_on=["s1"])]
    err = validate_plan(steps)
    assert err is not None
    assert "s1" in err
    assert "没有与用户对话的通道" in err
    assert "按合理默认" in err     # 错误串会被拼进下一次提示，必须指导模型怎么改


@pytest.mark.parametrize("desc", [
    "与用户沟通，确认需求",
    "询问用户的学习目标",
    "请用户提供当前水平",
    "等待用户回复后继续",
    "收集用户需求",
    "了解用户的当前水平",
])
def test_rejects_ask_user_variants(desc):
    assert validate_plan([PlanStep(id="s1", description=desc, expected="x")]) is not None


@pytest.mark.parametrize("desc", [
    "为用户生成 5 道练习题",
    "整理用户上传的资料并入库",
    "检索用户知识库中的 AI 相关资料",
    "根据用户目标拆解知识点",          # 「用户目标」是名词短语，不是提问动作
    "把总结导出为用户可下载的文件",
    "汇总各步产出，写出面向用户的最终答复",
])
def test_allows_legitimate_user_facing_steps(desc):
    """反向护栏：正则必须收窄到「交互动词 + 用户」，不能见到「用户」就拦。"""
    assert validate_plan([PlanStep(id="s1", description=desc, expected="x")]) is None


def test_ask_user_detected_in_expected_field_too():
    """有的模型把提问藏在 expected 里（description 写得中性）。"""
    steps = [PlanStep(id="s1", description="明确学习目标", expected="询问用户后得到的目标说明")]
    assert validate_plan(steps) is not None

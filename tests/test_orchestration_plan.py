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

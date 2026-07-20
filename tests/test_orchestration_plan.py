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
    """反向护栏：正则必须收窄到「交互动词 + 用户」，不能见到「用户」就拦。

    这里给 s1 连上 s0：本用例只测「问用户」这条规则，而「汇总各步产出」这类描述会命中
    另一条（引用前置产出须连依赖）。不隔离的话，两条规则会互相干扰、失败原因指向错的地方。
    """
    steps = [PlanStep(id="s0", description="搜集资料", expected="资料"),
             PlanStep(id="s1", description=desc, expected="x", depends_on=["s0"])]
    assert validate_plan(steps) is None


def test_ask_user_detected_in_expected_field_too():
    """有的模型把提问藏在 expected 里（description 写得中性）。"""
    steps = [PlanStep(id="s1", description="明确学习目标", expected="询问用户后得到的目标说明")]
    assert validate_plan(steps) is not None


# ---------- 未经要求不得写入知识库 ----------

def test_rejects_unrequested_knowledge_write():
    """回归：用户只说「制定 7 天 AI 学习计划」，计划却加了「将学习计划存入用户知识库」。
    知识库是用户自己整理的资料库，擅自写入会污染检索结果、事后还得手动清理。
    （提示词层已有同义约束但兜不住，故在此做确定性拦截。）"""
    steps = [PlanStep(id="s1", description="将学习计划内容存入用户知识库，以便后续检索和参考",
                      expected="已入库")]
    err = validate_plan(steps, "帮我制定一份 7 天的 AI 学习计划")
    assert err is not None and "s1" in err
    assert "并没有要求" in err and "污染" in err


def test_allows_knowledge_write_when_user_asked():
    steps = [PlanStep(id="s1", description="将整理好的资料存入知识库", expected="已入库")]
    assert validate_plan(steps, "把这些资料存进知识库") is None
    assert validate_plan(steps, "帮我收藏这份资料") is None


@pytest.mark.parametrize("desc", [
    "检索知识库中已有的 AI 发展相关资料",       # 读取，不是写入
    "从用户知识库检索资料并整理成提纲",
    "预先为各阶段出好配套练习题入库",           # 题库入库，与知识库无关
    "save_download 导出一份 Markdown 计划表",  # 交付物，不是往资料库塞东西
    "根据知识库资料撰写总结",
])
def test_allows_non_write_knowledge_steps(desc):
    """反向护栏：不能见到「知识库」就拦——读取与交付都是正当步骤。"""
    assert validate_plan([PlanStep(id="s1", description=desc, expected="x")], "写个总结") is None


def test_knowledge_rule_checks_expected_field_too():
    steps = [PlanStep(id="s1", description="整理内容", expected="内容已保存到知识库")]
    assert validate_plan(steps, "帮我整理") is not None


# ---------- 引用前置产出必须连依赖 ----------

def _two(desc, deps=()):
    return [PlanStep(id="s0", description="搜集资料", expected="资料"),
            PlanStep(id="s1", description=desc, expected="大纲", depends_on=list(deps))]


def test_rejects_step_referencing_prior_output_without_depends_on():
    """回归：步骤写「根据搜索结果整合…」却没连依赖。执行子步只收得到 depends_on 里的产出
    （executor._build_prompt 的 `if deps:`），依赖为空它手里一条搜索结果都没有，
    只能自己再搜一遍——多一轮往返，产出还和前一步对不上。"""
    err = validate_plan(_two("根据搜索结果整合并制定一份 7 天 AI 学习计划大纲"), "制定学习计划")
    assert err is not None and "s1" in err
    assert "depends_on" in err and "自己重新检索" in err


def test_allows_same_step_once_dependency_wired():
    assert validate_plan(
        _two("根据搜索结果整合并制定一份 7 天 AI 学习计划大纲", ("s0",)), "制定学习计划") is None


@pytest.mark.parametrize("desc", [
    "基于上一步的产出撰写总结",
    "汇总各步产出形成报告",
    "根据检索到的资料整理提纲",
])
def test_rejects_prior_output_variants(desc):
    assert validate_plan(_two(desc), "写报告") is not None


@pytest.mark.parametrize("desc", [
    "搜索关于 AI 未来发展的最新报道",     # 自身即检索，不是引用前置
    "检索知识库中的 AI 资料",
    "根据用户目标拆解知识点",             # 依据的是用户目标，不是前面的步骤
    "按入门水平排出 7 天日程",
])
def test_allows_self_contained_steps_without_deps(desc):
    """反向护栏：不能见到「搜索」「根据」就要求连依赖——首步本就无依赖可连。"""
    assert validate_plan(_two(desc), "制定计划") is None

import json
from harness.events import Progress, RunError, RunFinished, RunStarted, TextDelta
from harness.tools.base import ToolRegistry
import app.orchestration.orchestrator as om
from app.orchestration.orchestrator import Orchestrator
from app.orchestration.plan import Artifact, Plan, PlanStep, Verdict, Review
from app.orchestration.planner import PlannerError


# ---- 测试替身 ----
class FakePlanner:
    def __init__(self, plans, raise_on_plan=False):
        self._plans = list(plans); self._i = 0
        self._raise_on_plan = raise_on_plan
        self.seen_tools = []          # 记录每次收到的工具清单，供断言编排器确实传了
    async def plan(self, goal, recent_dialogue="", skill_hint="", *, tools_desc=""):
        self.seen_tools.append(tools_desc)
        if self._raise_on_plan:
            raise PlannerError("boom")
        p = self._plans[0]; return p
    async def replan(self, goal, plan, feedback, skill_hint="", *, tools_desc=""):
        self.seen_tools.append(tools_desc)
        self._i += 1
        return self._plans[min(self._i, len(self._plans) - 1)]


class FakeCritic:
    def __init__(self, validate_ok=True, reviews=(True,), validate_impossible=False):
        self._validate_ok = validate_ok
        self._validate_impossible = validate_impossible
        self._reviews = list(reviews); self._ri = 0
        self.last_dialogue = None   # review 收到的上文；未被调用时保持 None
    async def validate(self, step, artifact):
        return Verdict(ok=self._validate_ok, reason="",
                       impossible=(not self._validate_ok) and self._validate_impossible)
    async def review(self, goal, plan, artifacts, recent_dialogue=""):
        self.last_dialogue = recent_dialogue   # 供「上文有没有喂给裁判」的用例断言
        r = self._reviews[min(self._ri, len(self._reviews) - 1)]; self._ri += 1
        return Review(accept=r, feedback="补一下X")


# 编排器测试里的 Executor 替身一律以 **_kw 收尾：它们只是站位，真实签名由
# tests/test_orchestration_executor.py 钉着。两条并行的工作线都在给 execute() 加参数
# （done_effects / fx_sink），若每个替身都写死全部参数，每次加参数就要改十几处、
# 且合并时必冲突。替身该关心哪个参数就显式接哪个，其余交给 **_kw。
class FakeExecutor:
    """每步产出 summary=step.id 的 Artifact；记录执行顺序供并行断言。"""
    def __init__(self, order):
        self._order = order
    async def execute(self, step, deps, hint="", *, registry=None, goal="",
                      done_effects=None, fx_sink=None, **_kw):
        from app.orchestration.executor import StepArtifact
        self._order.append(step.id)
        yield Progress(f"subagent:executor:{step.id}", "开始")
        yield StepArtifact(Artifact(summary=f"done-{step.id}"))


def _mk(planner, critic, order, triage_simple=False, synth="最终答复", max_replan=2):
    async def fake_triage(msg, recent_dialogue=""):
        return triage_simple

    # run() 走的是 _triage（分流 + 意图一次算清）。两个都桩：只桩 _is_simple 的话
    # run() 会落到真实 _triage 上，而它在 __new__ 实例上够不着 _fast_complete，
    # 异常被兜底成「complex」——测试照样绿，却根本没在测 triage_simple 这个入参。
    async def fake_triage2(msg, recent_dialogue=""):
        return triage_simple, "chat"
    async def fake_synth(goal, artifacts, recent_dialogue=""):
        from harness.events import TextDelta
        yield TextDelta(text=synth)
    async def fake_simple(msg, budget=None, *, context=None, registry=None, prefer_main=False, skill_hint=""):
        yield RunFinished(message=__import__("harness.types", fromlist=["Message"]).Message(
            role=__import__("harness.types", fromlist=["Role"]).Role.ASSISTANT, content="简单答复"))
    orch = Orchestrator.__new__(Orchestrator)
    orch._planner = planner
    orch._critic = critic
    orch._executor = FakeExecutor(order)
    orch._is_simple = fake_triage
    orch._triage = fake_triage2
    orch._synthesize = fake_synth
    orch._simple_answer = fake_simple
    # 不桩 _simple_answer_verified：用真实方法。它内部委托 self._simple_answer（已桩 fake_simple）
    # 出草稿、再走 self._critic.review（FakeCritic 默认 accept）——1 步回退/规划降级在 verify=True
    # 时经它交付「简单答复」，链路天然成立，无需另桩。
    orch._max_replan = max_replan
    orch._max_step_retry = 2
    orch._budget = None
    orch._budget_factory = None
    # off_topic 判定用 _fast_complete；默认判「学习相关」(on_topic)，使现有测试行为不变、
    # 不被新增的无关拦截误伤。要测无关拦截的用例自行覆盖 _fast_complete 或 _is_off_topic。
    async def _fast_on_topic(system, user):
        return "on_topic"
    orch._fast_complete = _fast_on_topic
    return orch


async def _run(orch, msg="做点复杂的事"):
    return [ev async for ev in orch.run(msg)]


async def _run_kw(orch, msg, **kw):
    """带额外 run() 入参跑一轮（如 recent_dialogue）。"""
    return [ev async for ev in orch.run(msg, **kw)]


def _plan(*steps):
    return Plan(goal="g", steps=list(steps))


def _s(id, deps=()):
    return PlanStep(id=id, description=id, expected=id, depends_on=list(deps))


async def test_happy_path_emits_expected_event_sequence():
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2", deps=["s1"]))]),
               FakeCritic(validate_ok=True, reviews=(True,)), order)
    events = await _run(orch)
    assert isinstance(events[0], RunStarted)
    assert isinstance(events[-1], RunFinished)
    assert events[-1].message.content == "最终答复"
    assert any(isinstance(e, TextDelta) for e in events)   # synthesize 流式
    assert order == ["s1", "s2"]   # 依赖串行


async def test_parallel_steps_run_in_same_batch():
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2"))]),
               FakeCritic(reviews=(True,)), order)
    await _run(orch)
    assert set(order) == {"s1", "s2"}   # 两步无依赖，均执行


async def test_triage_short_circuit():
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(), order, triage_simple=True)
    events = await _run(orch, "你好")
    assert events[-1].message.content == "简单答复"
    assert order == []   # 未进编排


async def test_skill_match_emits_skill_progress():
    """路由命中技能时发 scope=skill 进度事件，前端「技能」块据此展示。"""
    from types import SimpleNamespace
    from harness.events import Progress
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(), order, triage_simple=True)
    orch._skill_matcher = SimpleNamespace(match=lambda msg: SimpleNamespace(
        name="错题精讲", description="精讲错题并举一反三", body="剧本正文"))
    events = await _run(orch, "帮我讲讲错题")
    skill_evs = [e for e in events if isinstance(e, Progress) and e.scope == "skill"]
    assert skill_evs, "命中技能应发 scope=skill 进度事件"
    assert "错题精讲" in skill_evs[0].text


async def test_reject_then_replan_then_accept():
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s1b")), _plan(_s("s2"))]),
               FakeCritic(validate_ok=True, reviews=(False, True)), order)
    events = await _run(orch)
    assert isinstance(events[-1], RunFinished)
    assert "s2" in order   # 重规划后的新步骤被执行


async def test_failed_step_blocking_dependent_degrades_gracefully():
    """合法计划里某步彻底 failed、阻塞其后继时，不该硬中止丢弃已完成成果，而应把不可达步
    标 skipped、带现有产物走终局定稿（spec §4「交终局 Critic 判」/§5「残缺胜过空手」）。"""
    order = []
    # 计划：s2 依赖 s1；s3 独立。critic 让 s1 校验恒失败 → s1 failed → s2 永远进不了就绪集；
    # s3 独立成功。期望：不 RunError，带 s3 成果 RunFinished。
    class PerStepCritic:
        async def validate(self, step, artifact):
            return Verdict(ok=(step.id != "s1"), reason="s1 bad")
        async def review(self, goal, plan, artifacts, recent_dialogue=""):
            return Review(accept=True, feedback="")
    got = {}
    async def capture_synth(goal, artifacts, recent_dialogue=""):
        got.update(artifacts)
        yield TextDelta(text="定稿")
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2", deps=["s1"]), _s("s3"))]),
               PerStepCritic(), order)
    orch._synthesize = capture_synth
    events = await _run(orch)
    assert not any(isinstance(e, RunError) for e in events)   # 不硬中止
    assert isinstance(events[-1], RunFinished)
    assert "s3" in got and "s1" not in got and "s2" not in got  # 带成功步成果、丢弃失败/阻塞步


async def test_replan_preserves_prior_done_artifacts():
    """重规划后，上一轮已完成步的产物必须仍进入终局 synthesize（spec §4「保留成果」）。"""
    order = []
    got = {}
    async def capture_synth(goal, artifacts, recent_dialogue=""):
        got.update(artifacts)
        yield TextDelta(text="定稿")
    # round1 计划 {s1}→done；review 先拒后受；replan → {s2}→done。终局须同时拿到 s1、s2。
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s1b")), _plan(_s("s2"))]),
               FakeCritic(validate_ok=True, reviews=(False, True)), order)
    orch._synthesize = capture_synth
    events = await _run(orch)
    assert isinstance(events[-1], RunFinished)
    assert "s1" in got and "s2" in got, f"重规划丢了上一轮产物：{sorted(got)}"


async def test_validate_fail_retries_bounded_then_failed():
    order = []
    # validate 恒失败：s1 会重试到 max_step_retry 后置 failed；review 放行 → 带残缺定稿
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2"))]),
               FakeCritic(validate_ok=False, reviews=(True,)), order)
    events = await _run(orch)
    assert isinstance(events[-1], RunFinished)   # 不因单步失败崩溃
    assert order.count("s1") == 2                # 初次 + 1 次重试（max_step_retry=2）


async def test_impossible_step_gives_up_immediately_no_retry():
    """结构性障碍（Critic 判 impossible）→ 终态放弃，只跑一次，不走满 max_step_retry。

    「做不到」与「没做好」是两回事：前者重试只会把同一句「我做不到」再说一遍，纯浪费。
    对照上面的普通失败（跑 2 次），此处 impossible 只跑 1 次。
    """
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2"))]),
               FakeCritic(validate_ok=False, validate_impossible=True, reviews=(True,)), order)
    events = await _run(orch)
    assert isinstance(events[-1], RunFinished)   # 仍带残缺成果收尾，不崩溃
    assert order.count("s1") == 1, "impossible 步不该重试"


async def test_downstream_of_impossible_step_is_skipped():
    """impossible 步终态失败后，依赖它的下游步进不了就绪集 → skipped，整轮不卡死。"""
    order = []
    plan = _plan(_s("s1"), _s("s2", deps=["s1"]))
    orch = _mk(FakePlanner([plan]),
               FakeCritic(validate_ok=False, validate_impossible=True, reviews=(True,)), order)
    await _run(orch)
    assert order.count("s1") == 1 and "s2" not in order   # s1 一次放弃，s2 从不执行
    assert plan.steps[0].status == "failed" and plan.steps[1].status == "skipped"


class _CountingCritic:
    def __init__(self): self.reviews = 0
    async def validate(self, step, artifact):
        return Verdict(ok=True, reason="")
    async def review(self, goal, plan, artifacts, recent_dialogue=""):
        self.reviews += 1
        return Review(accept=True, feedback="")


async def test_verify_false_skips_terminal_review():
    """结果校验关：跑完一轮直接汇总交付，不做终局 review/重规划。"""
    critic = _CountingCritic()
    orch = _mk(FakePlanner([_plan(_s("s1"))]), critic, [])
    events = [ev async for ev in orch.run("做点复杂的事", verify=False)]
    assert critic.reviews == 0, "verify=False 应跳过终局 review"
    assert isinstance(events[-1], RunFinished)


async def test_verify_true_runs_terminal_review():
    """结果校验开：终局 Critic review 照常运行。"""
    critic = _CountingCritic()
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2"))]), critic, [])
    events = [ev async for ev in orch.run("做点复杂的事", verify=True)]
    assert critic.reviews == 1, "verify=True 应运行终局 review"
    assert isinstance(events[-1], RunFinished)


async def test_planner_reasoning_emitted_before_plan():
    """开思考模式时，planner 的思考在计划之前发出（先思考→再出计划）。"""
    from harness.events import ReasoningDelta
    from app.orchestration.usage_ctx import record_reasoning

    class RPlanner:
        async def plan(self, goal, recent_dialogue="", skill_hint="", *, tools_desc=""):
            record_reasoning("先分析怎么拆")
            return _plan(_s("s1"), _s("s2"))
        async def replan(self, g, p, f, skill_hint="", *, tools_desc=""):
            return _plan(_s("s1"))

    orch = _mk(RPlanner(), FakeCritic(reviews=(True,)), [])
    events = [ev async for ev in orch.run("复杂")]
    # 规划思考走 Progress(scope=plan_reasoning) 独立通道，出现在第一个计划快照之前
    ri = next((i for i, e in enumerate(events)
               if isinstance(e, Progress) and e.scope == "plan_reasoning" and "先分析" in e.text), None)
    pi = next((i for i, e in enumerate(events)
               if isinstance(e, Progress) and e.scope == "plan"), None)
    assert ri is not None and pi is not None
    assert ri < pi, "规划思考应出现在第一个计划快照之前"
    # 末尾发一条带 elapsed_ms 的标记，供前端显示/刷新还原规划思考耗时
    assert any(isinstance(e, Progress) and e.scope == "plan_reasoning"
               and (e.detail or {}).get("elapsed_ms") is not None for e in events), \
        "应发出带 elapsed_ms 的规划思考耗时标记"


async def test_run_aggregates_all_usage_incl_planner_critic():
    """所有子调用的 token 用量（planner + executor + critic validate/review + synthesize）
    汇总成一条 ModelUsage，前端才显示得出总量。"""
    from harness.events import ModelUsage
    from harness.usage import Usage
    from app.orchestration.usage_ctx import record_usage
    from app.orchestration.executor import StepArtifact
    from app.orchestration.plan import Artifact

    class UExec:
        async def execute(self, step, deps, hint="", *, registry=None, goal="",
                          done_effects=None, fx_sink=None, **_kw):
            record_usage(Usage(0, 0, 100), 0.01)
            yield StepArtifact(Artifact(summary=f"done-{step.id}"))

    class UCritic:
        async def validate(self, step, art):
            record_usage(Usage(0, 0, 10), 0.001); return Verdict(ok=True, reason="")
        async def review(self, goal, plan, arts, recent_dialogue=""):
            record_usage(Usage(0, 0, 20), 0.002); return Review(accept=True, feedback="")

    class UPlanner:
        async def plan(self, goal, recent_dialogue="", skill_hint="", *, tools_desc=""):
            record_usage(Usage(0, 0, 30), 0.003); return _plan(_s("s1"), _s("s2"))
        async def replan(self, g, p, f, skill_hint="", *, tools_desc=""):
            return _plan(_s("s1"))

    async def usynth(goal, arts, recent_dialogue=""):
        record_usage(Usage(0, 0, 50), 0.005); yield TextDelta(text="答复")

    from harness.progress import set_emitter, reset_emitter
    orch = _mk(UPlanner(), UCritic(), [])
    orch._executor = UExec()
    orch._synthesize = usynth
    seen: list = []
    tok = set_emitter(seen.append)   # record_usage 经 emit 发逐调用增量 ModelUsage
    try:
        _ = [ev async for ev in orch.run("复杂")]
    finally:
        reset_emitter(tok)
    usages = [e for e in seen if isinstance(e, ModelUsage)]
    # plan 30 + s1/s2 各 100 + validate 各 10 + review 20 + synth 50 = 320（增量之和）
    assert sum(u.usage.total_tokens for u in usages) == 30 + 100 * 2 + 10 * 2 + 20 + 50
    assert abs(sum(u.cost_usd or 0 for u in usages)
               - (0.003 + 0.01 * 2 + 0.001 * 2 + 0.002 + 0.005)) < 1e-9


async def test_review_emits_verify_progress():
    """开结果校验：终局 Critic 的把关过程走 scope=verify（校验中…→通过），供前端 VerifyBadge 显示。"""
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2"))]),
               FakeCritic(validate_ok=True, reviews=(True,)), order)
    events = await _run(orch)
    verify = [e for e in events if isinstance(e, Progress) and e.scope == "verify"]
    assert any(e.status == "running" for e in verify), "应先发一条校验中(running)"
    assert any(e.status == "ok" and "通过" in e.text for e in verify), "通过时应发 ok 终态"


async def test_review_fail_emits_verify_error_then_retries():
    """校验不通过：先发 scope=verify(error) 带缺口说明，再重规划、最终通过再发一条 ok，形成校验历史。"""
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s1b")), _plan(_s("s1"))]),
               FakeCritic(validate_ok=True, reviews=(False, True)), order)
    events = await _run(orch)
    verify = [e for e in events if isinstance(e, Progress) and e.scope == "verify"]
    assert any(e.status == "error" and "补一下X" in e.text for e in verify), "不通过应发 error + 缺口"
    assert any(e.status == "ok" for e in verify), "重规划后通过应发 ok"


async def test_verify_off_emits_no_verify_progress():
    """关结果校验：跳过终局 Critic，不应发任何 scope=verify 事件。"""
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"))]),
               FakeCritic(validate_ok=True, reviews=(True,)), order)
    events = [ev async for ev in orch.run("做点复杂的事", verify=False)]
    assert not any(isinstance(e, Progress) and e.scope == "verify" for e in events)


async def test_record_usage_emits_per_call_increment_with_model():
    """设了 emitter 时，record_usage 每次发一条**本次调用的增量** ModelUsage（带 model 名），
    下游按模型累加得合计；未设 emitter（单测）时 no-op。"""
    from harness.events import ModelUsage
    from harness.usage import Usage
    from harness.progress import set_emitter, reset_emitter
    from app.orchestration.usage_ctx import UsageAcc, set_acc, reset_acc, record_usage

    seen: list = []
    etoken = set_emitter(seen.append)
    atoken = set_acc(UsageAcc())
    try:
        record_usage(Usage(0, 0, 10), 0.01, "m1")
        record_usage(Usage(0, 0, 5), 0.005, "m1")
        record_usage(Usage(0, 0, 7), 0.007, "m2")
    finally:
        reset_acc(atoken)
        reset_emitter(etoken)
    snaps = [e for e in seen if isinstance(e, ModelUsage)]
    assert len(snaps) == 3, "每次 record 发一条增量"
    assert [(s.usage.total_tokens, s.model) for s in snaps] == [(10, "m1"), (5, "m1"), (7, "m2")]
    # 按模型累加：m1=15、m2=7
    assert sum(s.usage.total_tokens for s in snaps if s.model == "m1") == 15


async def test_record_usage_no_emit_without_emitter():
    """无 emitter 时 record_usage 不发事件（保持对非编排器路径透明）。"""
    from harness.usage import Usage
    from app.orchestration.usage_ctx import UsageAcc, set_acc, reset_acc, record_usage
    atoken = set_acc(UsageAcc())
    try:
        record_usage(Usage(0, 0, 10), 0.01)   # 不应抛错，也无处可发
    finally:
        reset_acc(atoken)


async def test_synthesize_forwards_reasoning(make_mock):
    """开思考模式时，最终答复(synthesize)的思考过程应转发到前端，而不是被吞掉。"""
    from harness.llm.base import StreamChunk
    from harness.events import ReasoningDelta
    orch = Orchestrator.__new__(Orchestrator)
    orch._client = make_mock([[StreamChunk(type="reasoning", text="先想一下"),
                               StreamChunk(type="text", text="答复"),
                               StreamChunk(type="done")]])
    orch._model = "m"
    from app.orchestration.plan import Artifact
    evs = [ev async for ev in orch._synthesize("目标", {"s1": Artifact(summary="x")})]
    assert any(isinstance(e, ReasoningDelta) and "先想一下" in e.text for e in evs)
    assert any(isinstance(e, TextDelta) and "答复" in e.text for e in evs)


async def test_synthesize_falls_back_when_no_stream(make_mock):
    from harness.llm.base import StreamChunk
    orch = Orchestrator.__new__(Orchestrator)
    orch._client = make_mock([[StreamChunk(type="text", text="汇总答复"), StreamChunk(type="done")]])
    orch._model = "m"
    from app.orchestration.plan import Artifact
    evs = [ev async for ev in orch._synthesize("目标", {"s1": Artifact(summary="x")})]
    from harness.events import TextDelta
    assert any(isinstance(e, TextDelta) and "汇总答复" in e.text for e in evs)


async def test_planner_error_falls_back_to_simple_answer():
    order = []
    orch = _mk(FakePlanner([], raise_on_plan=True), FakeCritic(), order)
    events = await _run(orch)
    assert isinstance(events[-1], RunFinished)
    assert events[-1].message.content == "简单答复"
    assert order == []   # 从未进入编排/执行


async def test_running_step_emits_plan_snapshot():
    """就绪步一旦开跑就发一次计划快照（含 running），否则顶部任务步骤在执行中不显示进行态、不转圈。"""
    import json
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2"))]), FakeCritic(reviews=(True,)), order)
    events = await _run(orch)
    plan_snaps = [json.loads(e.text) for e in events
                  if isinstance(e, Progress) and e.scope == "plan"]
    # 至少有一份快照里存在 status=running 的步
    assert any(any(st["status"] == "running" for st in snap) for snap in plan_snaps), \
        "执行中应发出带 running 的计划快照"


async def test_plan_snapshots_carry_timing():
    """编排器给计划步计时：running 快照带 started_at_ms，done 步带 elapsed_ms（前端显示耗时）。"""
    import json
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2"))]), FakeCritic(reviews=(True,)), [])
    events = await _run(orch)
    snaps = [json.loads(e.text) for e in events
             if isinstance(e, Progress) and e.scope == "plan"]
    # 某快照里有 running 步带 started_at_ms
    assert any(any(st["status"] == "running" and st.get("started_at_ms") for st in snap)
               for snap in snaps), "running 步应带 started_at_ms"
    # 某快照里有 done 步带 elapsed_ms（非 None）
    assert any(any(st["status"] == "done" and st.get("elapsed_ms") is not None for st in snap)
               for snap in snaps), "done 步应带 elapsed_ms"


def test_plan_progress_includes_id_and_depends_on():
    """计划进度带上步骤 id 与 depends_on：前端据此把执行明细挂到对应步、并算并行/依赖关系。"""
    import json
    from app.orchestration.orchestrator import _plan_progress
    p = _plan_progress(_plan(_s("s1"), _s("s2"), _s("s3", deps=["s1", "s2"])))
    steps = json.loads(p.text)
    assert [s["id"] for s in steps] == ["s1", "s2", "s3"]
    assert steps[2]["depends_on"] == ["s1", "s2"]
    assert steps[0]["depends_on"] == []
    assert all("title" in s and "status" in s for s in steps)


async def test_budget_factory_fresh_per_run():
    """编排器是单例：每次 run 应经工厂新建独立预算，不跨轮累加、不写回 self。"""
    made = []
    class B:
        def start(self): pass
        def check(self): pass
    def factory():
        b = B(); made.append(b); return b
    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(reviews=(True,)), [])
    orch._budget_factory = factory
    await _run(orch)
    await _run(orch)
    assert len(made) == 2, "每次 run 应新建独立预算"
    assert getattr(orch, "_budget") is None, "预算不应写回 self（并发安全）"


async def test_budget_exceeded_degrades_to_synthesize():
    """预算超限：已完成步的成果尽力 synthesize 定稿，不硬中止（spec §5「残缺胜过空手」）。
    首检放行让 s1 跑完，随后超限 → s2 被 skip、带 s1 成果 RunFinished。"""
    from harness.reliability.budget import BudgetExceeded
    class StepBudget:
        def __init__(self): self.calls = 0
        def start(self): pass
        def check(self):
            self.calls += 1
            if self.calls > 1:          # 首检放行（s1 得以执行），其后一律超限
                raise BudgetExceeded("超预算")
    order = []
    got = {}
    async def capture_synth(goal, artifacts, recent_dialogue=""):
        got.update(artifacts)
        yield TextDelta(text="定稿")
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2", deps=["s1"]))]),
               FakeCritic(reviews=(True,)), order)
    orch._budget = StepBudget()
    orch._synthesize = capture_synth
    events = await _run(orch)
    from harness.events import RunError, RunFinished
    assert not any(isinstance(e, RunError) for e in events)   # 不硬中止
    assert isinstance(events[-1], RunFinished)
    assert "s1" in got and "s2" not in got   # 带已完成步成果、未跑的步不在内
    assert "s2" not in order                 # s2 被预算拦下，从未执行


async def test_parallel_steps_actually_concurrent():
    import asyncio
    from app.orchestration.executor import StepArtifact
    from app.orchestration.plan import Artifact
    class ProbeExecutor:
        def __init__(self):
            self.now = 0; self.peak = 0
        async def execute(self, step, deps, hint="", *, registry=None, goal="",
                          done_effects=None, fx_sink=None, **_kw):
            self.now += 1; self.peak = max(self.peak, self.now)
            await asyncio.sleep(0)
            self.now -= 1
            yield StepArtifact(Artifact(summary=f"done-{step.id}"))
    probe = ProbeExecutor()
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2"))]), FakeCritic(reviews=(True,)), [])
    orch._executor = probe
    await _run(orch)
    assert probe.peak >= 2   # 两个无依赖步真的同时在跑；顺序执行时 peak 恒为 1


async def test_early_abort_cancels_pending_workers():
    """提前放弃迭代（客户端断连/停止 → 生成器 aclose）时，同批未完成的 worker 必须被取消，
    不能变成继续跑 LLM 的悬挂任务。"""
    import asyncio
    from app.orchestration.executor import StepArtifact
    from app.orchestration.plan import Artifact
    cancelled: set[str] = set()

    class MixedExecutor:
        """s_fast 立刻产出一个事件；s_slow 阻塞，被取消时记录自己。"""
        async def execute(self, step, deps, hint="", *, registry=None, goal="",
                          done_effects=None, fx_sink=None, **_kw):
            if step.id == "s_slow":
                try:
                    await asyncio.sleep(100)
                except asyncio.CancelledError:
                    cancelled.add(step.id)
                    raise
                yield StepArtifact(Artifact(summary="done-s_slow"))
            else:
                yield Progress(f"subagent:executor:{step.id}", "开始")
                await asyncio.sleep(100)   # 让首个事件先被 yield 出去，随后也挂住
                yield StepArtifact(Artifact(summary="done-s_fast"))

    orch = _mk(FakePlanner([_plan(_s("s_fast"), _s("s_slow"))]), FakeCritic(), [])
    orch._executor = MixedExecutor()
    agen = orch._schedule_rounds(_plan(_s("s_fast"), _s("s_slow")), {})
    # 先跳过「就绪快照」（scope=plan），拉到首个 worker 事件 —— 此时两 worker 都已在途
    ev = await agen.__anext__()
    while isinstance(ev, Progress) and ev.scope == "plan":
        ev = await agen.__anext__()
    assert isinstance(ev, Progress)
    await agen.aclose()                      # 模拟提前放弃 → 应取消未完成 worker
    await asyncio.sleep(0)                    # 放行取消回调
    assert "s_slow" in cancelled             # 悬挂 worker 被取消（未取消时此断言失败）


async def test_run_threads_context_and_registry_to_simple_answer():
    """每请求 context/registry 应透传给简单直答（多轮/用户工具靠它）。"""
    seen = {}
    async def cap_simple(msg, budget=None, *, context=None, registry=None, prefer_main=False, skill_hint=""):
        seen["ctx"], seen["reg"] = context, registry
        yield RunFinished(message=__import__("harness.types", fromlist=["Message"]).Message(
            role=__import__("harness.types", fromlist=["Role"]).Role.ASSISTANT, content="简单答复"))
    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(), [], triage_simple=True)
    orch._simple_answer = cap_simple
    CTX, REG = object(), object()
    _ = [ev async for ev in orch.run("你好", context=CTX, registry=REG)]
    assert seen["ctx"] is CTX and seen["reg"] is REG


async def test_run_wraps_registry_as_hiding_view_for_executor():
    """执行子步拿到的是每请求 registry 的隐藏视图（隐藏 update_plan），非裸 registry。"""
    from harness.tools.base import ToolRegistry
    from app.orchestration.executor import HidingRegistry
    seen = {}
    class CapExec:
        async def execute(self, step, deps, hint="", *, registry=None, goal="",
                          done_effects=None, fx_sink=None, **_kw):
            from app.orchestration.executor import StepArtifact
            seen["reg"] = registry
            yield StepArtifact(Artifact(summary="x"))
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2"))]), FakeCritic(reviews=(True,)), [])
    orch._executor = CapExec()
    reg = ToolRegistry()
    _ = [ev async for ev in orch.run("复杂", registry=reg)]
    assert isinstance(seen["reg"], HidingRegistry)
    assert seen["reg"].get("update_plan") is None      # 隐藏了 update_plan


async def test_run_passes_recent_dialogue_to_planner_and_synth():
    """最近对话应喂给 Planner（上下文相关拆分）与最终汇总。"""
    seen = {}
    class CapPlanner:
        async def plan(self, goal, recent_dialogue="", skill_hint="", *, tools_desc=""):
            seen["plan_rd"] = recent_dialogue
            return _plan(_s("s1"), _s("s2"))
        async def replan(self, g, p, f, skill_hint="", *, tools_desc=""):
            return _plan(_s("s1"))
    async def cap_synth(goal, artifacts, recent_dialogue=""):
        seen["synth_rd"] = recent_dialogue
        yield TextDelta(text="定稿")
    orch = _mk(CapPlanner(), FakeCritic(reviews=(True,)), [])
    orch._synthesize = cap_synth
    _ = [ev async for ev in orch.run("复杂", recent_dialogue="最近对话X")]
    assert seen["plan_rd"] == "最近对话X" and seen["synth_rd"] == "最近对话X"


async def test_run_backcompat_no_per_request_deps():
    """不传每请求依赖时仍正常跑（对既有测试/调用透明）。"""
    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(reviews=(True,)), [])
    events = await _run(orch)
    assert isinstance(events[-1], RunFinished)


async def test_run_uses_passed_run_id_for_run_started():
    """run(run_id=X) → RunStarted 带 X：使本轮所有事件经 sink 归到 chat 登记进 conversation_runs
    的那个 run_id，否则按用户过滤的运行统计会把编排器事件全滤掉。缺省仍自造 uuid。"""
    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(), [], triage_simple=True)
    ev_fixed = [ev async for ev in orch.run("hi", run_id="fixed-id")]
    assert isinstance(ev_fixed[0], RunStarted) and ev_fixed[0].run_id == "fixed-id"
    ev_auto = [ev async for ev in orch.run("hi")]                    # 未传 → 自造
    assert isinstance(ev_auto[0], RunStarted) and ev_auto[0].run_id != "fixed-id"


def test_obvious_simple_heuristic():
    from app.orchestration.orchestrator import _obvious_simple
    assert _obvious_simple("你好") and _obvious_simple("谢谢！") and _obvious_simple("  ok ")
    assert _obvious_simple("thanks") and _obvious_simple("嗯嗯") and _obvious_simple("晚上好~")
    assert not _obvious_simple("你好，帮我查资料")   # 带任务，不短路
    assert not _obvious_simple("解释一下光合作用")
    assert not _obvious_simple("考我5道题")
    assert not _obvious_simple("")


async def test_greeting_short_circuits_without_llm_triage():
    """纯寒暄应零成本短路：不调 LLM triage，直接简单直答。"""
    from harness.types import Message, Role
    calls = {"triage": 0}
    async def counting_triage(msg, recent_dialogue=""):
        calls["triage"] += 1
        return False
    hit = {"simple": 0}
    async def cap_simple(msg, budget=None, *, context=None, registry=None, prefer_main=False, skill_hint=""):
        hit["simple"] += 1
        yield RunFinished(message=Message(role=Role.ASSISTANT, content="hi"))
    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(), [])
    orch._is_simple = counting_triage
    orch._simple_answer = cap_simple
    events = [ev async for ev in orch.run("你好")]
    assert calls["triage"] == 0, "寒暄不应调 LLM triage"
    assert hit["simple"] == 1 and isinstance(events[-1], RunFinished)


async def test_non_greeting_still_uses_llm_triage():
    """非寒暄消息仍交 LLM triage 判简单/复杂（同一次调用顺带产出意图类别）。"""
    calls = {"triage": 0}
    async def counting_triage(msg, recent_dialogue=""):
        calls["triage"] += 1
        return True, "code"
    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(), [])
    orch._triage = counting_triage
    _ = [ev async for ev in orch.run("帮我分析这段代码的时间复杂度")]
    assert calls["triage"] == 1, "非寒暄仍须走 LLM triage，且只走一次（意图搭车、不额外调用）"


async def test_force_simple_bypasses_triage_and_planning():
    """force_simple=True（考试等有状态交互）应绕过 triage 与 plan-execute-synthesize，直走简单直答。

    覆盖 Bug：考试请求被 triage 判成复杂 → 走多步规划+二次汇总，会吞掉 start_exam 原样呈现的
    第一题、且重试会重置考试。force_simple 应在 triage 之前短路，不 triage、不规划、不执行任何步。"""
    from harness.types import Message, Role
    calls = {"triage": 0, "plan": 0}
    order = []

    class SpyPlanner:
        async def plan(self, goal, recent_dialogue="", skill_hint="", *, tools_desc=""):
            calls["plan"] += 1
            return _plan(_s("s1"))
        async def replan(self, goal, plan, feedback, skill_hint="", *, tools_desc=""):
            return _plan(_s("s1"))

    async def counting_triage(msg, recent_dialogue=""):
        calls["triage"] += 1
        return False   # 判复杂：只有真正短路才不会走到规划

    seen = {}
    async def cap_simple(msg, budget=None, *, context=None, registry=None, prefer_main=False, skill_hint=""):
        seen["prefer_main"] = prefer_main
        yield RunFinished(message=Message(role=Role.ASSISTANT, content="简单答复"))

    orch = _mk(SpyPlanner(), FakeCritic(), order, triage_simple=False)
    orch._is_simple = counting_triage
    orch._simple_answer = cap_simple
    events = [ev async for ev in orch.run("从题库抽5道题考考我", force_simple=True)]
    assert isinstance(events[0], RunStarted) and isinstance(events[-1], RunFinished)
    assert events[-1].message.content == "简单答复"   # 走了 _simple_answer
    assert calls["triage"] == 0 and calls["plan"] == 0   # 未 triage、未规划
    assert order == []                                    # 未执行任何计划步
    assert seen["prefer_main"] is True                    # 考试走主模型（可靠逐题推进）


def test_orchestrator_uses_fast_model_for_simple_answer():
    """简单直答走快速档 client/model（省钱提速）。"""
    orch = Orchestrator.__new__(Orchestrator)
    orch.__init__(client="MAIN", registry=None, model="main-model",
                  planner=None, critic=None, executor=None, fast_complete=None,
                  fast_client="FAST", fast_model="fast-model")
    assert orch._fast_client == "FAST" and orch._fast_model == "fast-model"


async def test_simple_answer_prefer_main_uses_main_client_no_reclamp(monkeypatch):
    """prefer_main=True（考试）→ 用主 client/model，且不按快速档重裁窗口（避免截断携带下一题的
    最后一条消息）；prefer_main=False → 快速档 client/model 且按其窗口重裁。"""
    import app.orchestration.orchestrator as orch_mod
    from harness.context.manager import ContextManager
    from harness.types import Message, Role
    cap: list[dict] = []

    class FakeLoop:
        def __init__(self, *, client, registry, context, max_steps, model_name, budget=None):
            cap.append({"client": client, "model": model_name, "context": context})
        async def run(self, message):
            yield RunFinished(message=Message(role=Role.ASSISTANT, content="ok"))

    monkeypatch.setattr(orch_mod, "AgentLoop", FakeLoop)
    orch = Orchestrator.__new__(Orchestrator)
    orch._client, orch._model = "MAIN", "main-model"
    orch._fast_client, orch._fast_model = "FAST", "fast-model"
    orch._fast_max_prompt_tokens = 999999          # >0：非主档时会触发重裁
    orch._registry = None
    orch._simple_max_steps = 20                     # 简单直答步数上限（本测不关心具体值，仅需存在）
    ctx_obj = ContextManager("sys")

    _ = [ev async for ev in orch._simple_answer("hi", context=ctx_obj, prefer_main=True)]
    assert cap[-1]["client"] == "MAIN" and cap[-1]["model"] == "main-model"
    assert cap[-1]["context"] is ctx_obj           # 主档不重裁，原样传入

    _ = [ev async for ev in orch._simple_answer("hi", context=ctx_obj, prefer_main=False)]
    assert cap[-1]["client"] == "FAST" and cap[-1]["model"] == "fast-model"
    assert cap[-1]["context"] is not ctx_obj       # 快速档按窗口重裁（被 Clamp 包裹）


def test_orchestrator_fast_falls_back_to_main_when_unset():
    """未配快速模型 → 回退主 client/主模型（零行为变更）。"""
    orch = Orchestrator.__new__(Orchestrator)
    orch.__init__(client="MAIN", registry=None, model="main-model",
                  planner=None, critic=None, executor=None, fast_complete=None)
    assert orch._fast_client == "MAIN" and orch._fast_model == "main-model"


async def test_run_emits_per_model_usage():
    """各子调用 record_usage 经 emit 发**逐模型增量** ModelUsage（带 model 名），供落 trajectory
    分模型统计；同模型的多次增量累加即该模型总量。"""
    from harness.events import ModelUsage
    from harness.usage import Usage
    from harness.progress import set_emitter, reset_emitter
    from app.orchestration.usage_ctx import record_usage
    from app.orchestration.executor import StepArtifact
    from app.orchestration.plan import Artifact

    class MExec:
        async def execute(self, step, deps, hint="", *, registry=None, goal="",
                          done_effects=None, fx_sink=None, **_kw):
            record_usage(Usage(0, 0, 100), 0.01, "fast-model")
            yield StepArtifact(Artifact(summary=f"done-{step.id}"))

    class MCritic:
        async def validate(self, step, art):
            record_usage(Usage(0, 0, 10), 0.001, "fast-model"); return Verdict(ok=True, reason="")
        async def review(self, goal, plan, arts, recent_dialogue=""):
            record_usage(Usage(0, 0, 20), 0.002, "main-model"); return Review(accept=True, feedback="")

    class MPlanner:
        async def plan(self, goal, recent_dialogue="", skill_hint="", *, tools_desc=""):
            record_usage(Usage(0, 0, 30), 0.003, "main-model"); return _plan(_s("s1"), _s("s2"))
        async def replan(self, g, p, f, skill_hint="", *, tools_desc=""):
            return _plan(_s("s1"))

    async def msynth(goal, arts, recent_dialogue=""):
        record_usage(Usage(0, 0, 50), 0.005, "main-model"); yield TextDelta(text="答复")

    orch = _mk(MPlanner(), MCritic(), [])
    orch._executor = MExec()
    orch._synthesize = msynth
    seen: list = []
    tok = set_emitter(seen.append)
    try:
        _ = [ev async for ev in orch.run("复杂")]
    finally:
        reset_emitter(tok)
    usages = [e for e in seen if isinstance(e, ModelUsage)]
    agg: dict = {}
    for u in usages:
        e = agg.setdefault(u.model, {"tok": 0, "cost": 0.0})
        e["tok"] += u.usage.total_tokens; e["cost"] += u.cost_usd or 0.0
    # 注：这里的 "fast-model"/"main-model" 是各 mock 自己塞进 record_usage 的**任意标签**，
    # 只为验证「用量按模型名分桶聚合」这一逻辑，不代表真实分档（真实里 validate/review 走 judge）。
    assert set(agg) == {"fast-model", "main-model"}
    assert agg["fast-model"]["tok"] == 220      # 两步：exec(100)×2 + validate(10)×2 都标了 fast-model
    assert agg["main-model"]["tok"] == 100      # 本 mock 把 plan(30)+review(20)+synth(50) 标了 main-model
    assert abs(agg["fast-model"]["cost"] - 0.022) < 1e-9
    assert abs(agg["main-model"]["cost"] - 0.010) < 1e-9


# ---------- 工具清单下发：规划器必须看到执行子步真正拿得到的工具 ----------

async def test_planner_receives_exec_registry_roster_without_update_plan():
    """回归：规划器看不到工具清单时会编出系统做不到的步骤（如「保存到 Notion」），
    执行子步读到那种描述便不会调真实工具。清单须取执行子步视图——update_plan 被隐藏，
    规划器不该把它排进计划。"""
    from harness.tools.base import Tool, ToolRegistry
    from pydantic import BaseModel

    class _T(Tool):
        class Params(BaseModel):
            x: str = ""
        def __init__(self, name, desc):
            self.name = name; self.description = desc
        async def run(self, params):
            return ""

    reg = ToolRegistry()
    reg.register(_T("save_to_knowledge", "把内容存入用户知识库。"))
    reg.register(_T("update_plan", "更新计划。"))
    planner = FakePlanner([_plan(PlanStep(id="s1", description="a", expected="b"))])
    orch = _mk(planner, FakeCritic(), [])
    orch._registry = reg
    [ev async for ev in orch.run("搜索最新 AI 资讯并保存到知识库", registry=reg)]

    roster = planner.seen_tools[0]
    assert "save_to_knowledge" in roster
    assert "update_plan" not in roster        # 执行子步看不到它，规划器也不该看到


# ---------- 命中技能后不重拆步骤 ----------

class _FakeMatched:
    name = "联网调研"
    description = "搜集资料并整理"
    body = "1. 搜索  2. 筛选  3. 汇总"


class _FakeMatcher:
    def match(self, msg):
        return _FakeMatched()


async def test_skill_hit_does_not_replan_on_review_reject():
    """命中技能时，终局校验不通过也不重新拆解——技能剧本就是既定流程，重拆等于推翻它，
    用户会看到步骤中途凭空变样。单步做砸由 max_step_retry 在原步骤内兜住，与此无关。"""
    planner = FakePlanner([_plan(_s("s1"), _s("s1b")), _plan(_s("s2"))])
    orch = _mk(planner, FakeCritic(validate_ok=True, reviews=(False, True)), [])
    orch._skill_matcher = _FakeMatcher()
    events = await _run(orch)

    # replan 未被调用：FakePlanner 的第二份计划（s2）不该出现
    assert planner._i == 0, "命中技能不应触发重规划"
    plans = [e for e in events if isinstance(e, Progress) and e.scope == "plan"]
    assert all("s2" not in (p.text or "") for p in plans), "步骤被重新拆解了"
    assert isinstance(events[-1], RunFinished)          # 仍带现有产物定稿
    assert any(isinstance(e, Progress) and e.scope == "verify"
               and "不重新拆解步骤" in (e.text or "") for e in events)


async def test_no_skill_still_replans_on_reject():
    """反向：没命中技能时，重规划照旧——别把这条护栏做成全局禁用重规划。"""
    planner = FakePlanner([_plan(_s("s1"), _s("s1b")), _plan(_s("s2"))])
    orch = _mk(planner, FakeCritic(validate_ok=True, reviews=(False, True)), [])
    await _run(orch)
    assert planner._i == 1, "无技能时应正常重规划"


async def test_replan_receives_skill_hint():
    """防漏传：replan 的签名要能接住技能剧本，否则新计划在「不知道有技能」的前提下重拆。"""
    import inspect
    from app.orchestration.planner import Planner
    assert "skill_hint" in inspect.signature(Planner.replan).parameters


# ---------- 用户拒绝 = 终态失败，不重试 ----------

class _DenyingExecutor:
    """模拟子步里的操作被用户拒绝：产出终态失败标记。"""
    def __init__(self, order):
        self._order = order

    async def execute(self, step, deps, hint="", *, registry=None, goal="",
                      done_effects=None, fx_sink=None, **_kw):
        from app.orchestration.executor import StepArtifact
        self._order.append(step.id)
        yield Progress(f"subagent:executor:{step.id}", "开始")
        yield StepArtifact(Artifact(summary="命令未执行：用户拒绝了该操作"), terminal=True)


async def test_user_denial_is_terminal_no_retry():
    """回归：拒绝后步骤校验失败 → 编排器按普通失败重试，把同一个弹窗又怼给用户几次。
    重试只对偶发故障有意义；人已经说了不，重跑不会有不同答案。"""
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2", deps=["s1"]))]),
               FakeCritic(validate_ok=False, reviews=(True,)), order)
    orch._executor = _DenyingExecutor(order)
    events = await _run(orch)

    assert order == ["s1"], f"被拒的步骤不该重跑，实际执行了 {len(order)} 次"
    assert isinstance(events[-1], RunFinished)      # 仍正常收尾，不硬崩


async def test_ordinary_failure_still_retries():
    """反向：普通失败照旧重试——别把这条护栏做成全局禁用重试。"""
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2"))]),
               FakeCritic(validate_ok=False, reviews=(True,)), order)
    await _run(orch)
    assert order.count("s1") == 2      # 初次 + 1 次重试（max_step_retry=2）


# ---------- 执行子步不得自作主张写知识库 ----------

def _reg_with_kb():
    from harness.tools.base import Tool, ToolRegistry
    from pydantic import BaseModel

    class _T(Tool):
        class Params(BaseModel):
            x: str = ""
        def __init__(self, name):
            self.name = name; self.description = "d"
        async def run(self, params):
            return ""

    reg = ToolRegistry()
    for n in ("save_to_knowledge", "save_download", "search_knowledge", "update_plan"):
        reg.register(_T(n))
    return reg


class _CapturingExecutor:
    """记录每步实际拿到的工具视图。"""
    def __init__(self):
        self.seen = []

    async def execute(self, step, deps, hint="", *, registry=None, goal="",
                      done_effects=None, fx_sink=None, **_kw):
        from app.orchestration.executor import StepArtifact
        self.seen.append({t.name for t in registry.tools()} if registry else set())
        yield StepArtifact(Artifact(summary="done"))


async def _run_with(msg, executor):
    # 两步：1 步计划会回退简单直答、执行器捕不到 registry，故加独立 s2 让编排真的跑
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2"))]), FakeCritic(), [])
    orch._executor = executor
    reg = _reg_with_kb()
    orch._registry = reg
    [ev async for ev in orch.run(msg, registry=reg)]
    return executor.seen[0]


async def test_kb_write_tool_hidden_from_substeps_when_not_requested():
    """回归：步骤文本干净（「归纳成结构化学习笔记」），子步却自己调 save_to_knowledge
    把笔记塞进了用户知识库。validate_plan 只看计划文本、拦不到执行期的自作主张，
    故在工具层面直接不给。"""
    tools = await _run_with("整理这些 AI 资料，归纳成学习笔记", _CapturingExecutor())
    assert "save_to_knowledge" not in tools
    assert "update_plan" not in tools          # 既有隐藏项不受影响
    assert "save_download" in tools            # 交付物工具照常可用
    assert "search_knowledge" in tools         # 读取知识库不受影响


async def test_kb_write_tool_available_when_user_asks():
    tools = await _run_with("把这些资料整理好存进知识库", _CapturingExecutor())
    assert "save_to_knowledge" in tools


async def test_kb_write_tool_available_when_skill_prescribes_it():
    """命中以入库为目的的技能（如「资料入库」）时不隐藏，否则会把技能本身弄坏。"""
    class _M:
        def match(self, msg):
            class _S:
                name = "资料入库"; description = "d"
                body = "1. 读料  2. 用 save_to_knowledge 入库"
            return _S()
    ex = _CapturingExecutor()
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2"))]), FakeCritic(), [])
    orch._executor = ex
    orch._skill_matcher = _M()
    reg = _reg_with_kb(); orch._registry = reg
    [e async for e in orch.run("整理这份讲义", registry=reg)]
    assert "save_to_knowledge" in ex.seen[0]


# ---------- 命中技能即走规划 ----------

class _SkillMatcherStub:
    def __init__(self, body="1. 联网查  2. 整理  3. 入库"):
        self._body = body

    def match(self, msg):
        class _S:
            name = "联网调研"; description = "d"
        _S.body = self._body
        return _S()


async def test_skill_hit_forces_planning_even_if_triage_says_simple():
    """回归：技能剧本本身就是多步流程，却因 triage 判 simple 而被塞进单循环——
    结果不出计划步，模型还可能自己发一份没有 id 的 ReAct 清单把工具块吞掉。"""
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2"))]), FakeCritic(), order, triage_simple=True)
    orch._skill_matcher = _SkillMatcherStub()
    events = await _run(orch, "搜索最新的 AI 资讯，保存到知识库")

    assert order == ["s1", "s2"], "命中技能应走规划执行，而非简单直答"
    assert any(isinstance(e, Progress) and e.scope == "plan" for e in events), "应发出计划"
    assert events[-1].message.content == "最终答复"       # 走的是编排汇总，不是简单直答


async def test_skill_hit_skips_triage_call():
    """命中技能时结论已定，不必再花一次 triage 调用。"""
    called = {"n": 0}
    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(), [])

    async def _counting_triage(msg, recent_dialogue=""):
        called["n"] += 1
        return True
    orch._is_simple = _counting_triage
    orch._skill_matcher = _SkillMatcherStub()
    await _run(orch, "搜索最新的 AI 资讯")
    assert called["n"] == 0


async def test_no_skill_still_honours_triage():
    """反向：没命中技能时 triage 说了算——别把这条改动做成「永远不走简单直答」。"""
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(), order, triage_simple=True)
    events = await _run(orch, "你好")
    assert order == [] and events[-1].message.content == "简单答复"


async def test_exam_stays_single_loop_even_with_skill_matcher():
    """考试（force_simple）永远走单循环：逐题推进不能被拆成计划。"""
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(), order, triage_simple=False)
    orch._skill_matcher = _SkillMatcherStub()
    events = [e async for e in orch.run("下一题", force_simple=True)]
    assert order == [] and events[-1].message.content == "简单答复"

def test_synth_user_does_not_glue_step_id_to_content():
    """汇总提示词同样不能把步骤 id 粘在正文前，否则最终答复里会漏出 [s1] 残留。"""
    from app.orchestration.orchestrator import _synth_user
    from app.orchestration.plan import Artifact
    out = _synth_user("目标", {"s1": Artifact(summary="# 标题\n正文")})
    assert "[s1] # 标题" not in out
    assert "# 标题" in out and "s1" in out


# ---- 考试轮的终局校验（force_simple + verify）----
#
# 考试的判分/错题入库/游标推进都由服务端 grade_exam_turn 确定性完成，模型只负责讲解与
# 呈现下一题。此前 force_simple 把整条编排器路径连同终局 Critic 一起关掉，讲解讲错
# （判定说反、漏告知「已存入错题集」、篡改下一题题面）无人兜底，且前端"结果校验"开关
# 在考试轮完全失效——开着也收不到一条 verify 事件。

def _exam_orch(reviews, captured):
    """构造考试轮编排器：_simple_answer 产出 TextDelta，并记录每次调用拿到的 registry。"""
    from harness.types import Message, Role

    async def cap_simple(msg, budget=None, *, context=None, registry=None,
                         prefer_main=False, skill_hint=""):
        captured.append({"msg": msg, "registry": registry, "prefer_main": prefer_main})
        text = f"第{len(captured)}版讲解"
        yield TextDelta(text=text)
        yield RunFinished(message=Message(role=Role.ASSISTANT, content=text))

    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(reviews=reviews), [])
    orch._simple_answer = cap_simple
    return orch


async def test_exam_turn_runs_final_review():
    """考试轮 + 结果校验开 → 跑终局 Critic，并发出前端徽章依赖的 verify 事件。"""
    cap = []
    orch = _exam_orch((True,), cap)
    events = [ev async for ev in orch.run("B", verify=True, force_simple=True)]
    vp = [e.text for e in events if isinstance(e, Progress) and e.scope == "verify"]
    assert vp == ["结果校验中…", "结果校验通过"]
    assert len(cap) == 1                               # 通过 → 不重答
    assert events[-1].message.content == "第1版讲解"


async def test_exam_turn_verify_off_skips_review():
    """考试轮 + 结果校验关 → 一条 verify 事件都不发（维持原有的快路径）。"""
    cap = []
    orch = _exam_orch((True,), cap)
    events = [ev async for ev in orch.run("B", verify=False, force_simple=True)]
    assert not [e for e in events if isinstance(e, Progress) and e.scope == "verify"]
    assert len(cap) == 1


async def test_exam_turn_failed_review_redoes_without_exam_tools():
    """校验不过 → 清屏 + 就地重答一次；重答给空工具表。

    重答留着工具，模型可能再调一次 start_exam 把考试进度整个重置——这正是当初
    整条关掉校验的理由。考试轮的原料（判定结论/正确答案/解析/下一题题面）都已在
    上下文里，重答只是重新组织文字，不需要任何工具。
    """
    cap = []
    orch = _exam_orch((False, True), cap)
    base_reg = ToolRegistry()
    events = [ev async for ev in orch.run("B", verify=True, force_simple=True,
                                          registry=base_reg)]
    scopes = [(e.scope, e.text) for e in events if isinstance(e, Progress)]
    assert ("verify", "补一下X") in scopes             # 未过的原因透给前端
    assert ("reset", "") in scopes                     # 清屏，避免两版拼接
    assert len(cap) == 2                               # 重答了一次
    assert cap[0]["registry"] is base_reg              # 第一版：正常工具表
    assert cap[1]["registry"].tools() == []            # 重答：空工具表，碰不到 start_exam
    assert "结果校验" in cap[1]["msg"]                  # 重答指令带上了 Critic 的意见
    assert events[-1].message.content == "第2版讲解"    # 交付的是重答那版


async def test_exam_turn_first_run_finished_suppressed_until_review():
    """第一版的 RunFinished 必须压到校验有结论之后才发。

    它是终结信号，提前发出去前端立刻标「已完成」，而这一版随时可能被重答顶掉。
    """
    cap = []
    orch = _exam_orch((False, True), cap)
    events = [ev async for ev in orch.run("B", verify=True, force_simple=True)]
    assert len([e for e in events if isinstance(e, RunFinished)]) == 1
    # 校验事件必须早于唯一那条 RunFinished
    vi = next(i for i, e in enumerate(events)
              if isinstance(e, Progress) and e.scope == "verify")
    fi = next(i for i, e in enumerate(events) if isinstance(e, RunFinished))
    assert vi < fi


async def test_plain_simple_turn_still_skips_review():
    """非考试的简单轮维持原样：不校验。

    校验寒暄没有意义，且每轮多一次主模型往返会拖慢最快的那条路径。
    """
    cap = []
    orch = _exam_orch((True,), cap)
    events = [ev async for ev in orch.run("你好", verify=True)]   # 命中 _obvious_simple
    assert not [e for e in events if isinstance(e, Progress) and e.scope == "verify"]
    assert len(cap) == 1


# ---------- 用量事件必须带上真实延迟与重试次数 ----------

async def test_record_usage_preserves_latency_and_attempts():
    """回归：编排器重发 ModelUsage 时把 latency_ms/attempts 写死成 0/1，而它是唯一主流程
    ——落进 trajectory 的用量事件几乎全走这里，于是「AI 运行统计」的平均延迟、p95 延迟
    恒为 0，重试次数也恒为 0（retries 靠 attempts>1 判定）。丢字段不会报错，只是整列空白。"""
    from harness.events import ModelUsage
    from harness.usage import Usage
    from harness import progress as _p
    from app.orchestration.usage_ctx import record_usage

    got = []
    tok = _p.set_emitter(got.append)
    try:
        record_usage(Usage(10, 20, 30), 0.001, "m1", 1234.5, 3)
    finally:
        _p.reset_emitter(tok)

    ev = next(e for e in got if isinstance(e, ModelUsage))
    assert ev.latency_ms == 1234.5
    assert ev.attempts == 3
    assert ev.model == "m1"


async def test_executor_passes_through_model_usage_latency():
    """执行子步收到内核的 ModelUsage 后要把延迟带下去，而不是自己造一条空的。"""
    from harness.events import ModelUsage, RunFinished
    from harness.types import Message, Role
    from harness.usage import Usage
    from harness import progress as _p
    from app.orchestration.executor import Executor
    from app.orchestration.plan import PlanStep

    class _Loop:
        def __init__(self, *a, **k): pass
        async def run(self, prompt):
            yield ModelUsage(usage=Usage(1, 2, 3), cost_usd=0.0,
                             attempts=2, latency_ms=888.0, model="m1")
            yield RunFinished(message=Message(role=Role.ASSISTANT, content="ok"))

    import app.orchestration.executor as _ex
    orig, _ex.AgentLoop = _ex.AgentLoop, _Loop
    got = []
    tok = _p.set_emitter(got.append)
    try:
        ex = Executor(client=None, registry=None, system_prompt="s", model="m")
        async for _ in ex.execute(PlanStep(id="s1", description="d", expected="e"), {}):
            pass
    finally:
        _ex.AgentLoop = orig
        _p.reset_emitter(tok)

    ev = next(e for e in got if isinstance(e, ModelUsage))
    assert ev.latency_ms == 888.0 and ev.attempts == 2

def test_synth_user_does_not_glue_step_id_to_content():
    """汇总提示词同样不能把步骤 id 粘在正文前，否则最终答复里会漏出 [s1] 残留。"""
    from app.orchestration.orchestrator import _synth_user
    from app.orchestration.plan import Artifact
    out = _synth_user("目标", {"s1": Artifact(summary="# 标题\n正文")})
    assert "[s1] # 标题" not in out
    assert "# 标题" in out and "s1" in out


# ---- 考试轮的终局校验（force_simple + verify）----
#
# 考试的判分/错题入库/游标推进都由服务端 grade_exam_turn 确定性完成，模型只负责讲解与
# 呈现下一题。此前 force_simple 把整条编排器路径连同终局 Critic 一起关掉，讲解讲错
# （判定说反、漏告知「已存入错题集」、篡改下一题题面）无人兜底，且前端"结果校验"开关
# 在考试轮完全失效——开着也收不到一条 verify 事件。

def _exam_orch(reviews, captured):
    """构造考试轮编排器：_simple_answer 产出 TextDelta，并记录每次调用拿到的 registry。"""
    from harness.types import Message, Role

    async def cap_simple(msg, budget=None, *, context=None, registry=None,
                         prefer_main=False, skill_hint=""):
        captured.append({"msg": msg, "registry": registry, "prefer_main": prefer_main})
        text = f"第{len(captured)}版讲解"
        yield TextDelta(text=text)
        yield RunFinished(message=Message(role=Role.ASSISTANT, content=text))

    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(reviews=reviews), [])
    orch._simple_answer = cap_simple
    return orch


async def test_exam_turn_runs_final_review():
    """考试轮 + 结果校验开 → 跑终局 Critic，并发出前端徽章依赖的 verify 事件。"""
    cap = []
    orch = _exam_orch((True,), cap)
    events = [ev async for ev in orch.run("B", verify=True, force_simple=True)]
    vp = [e.text for e in events if isinstance(e, Progress) and e.scope == "verify"]
    assert vp == ["结果校验中…", "结果校验通过"]
    assert len(cap) == 1                               # 通过 → 不重答
    assert events[-1].message.content == "第1版讲解"


async def test_exam_turn_verify_off_skips_review():
    """考试轮 + 结果校验关 → 一条 verify 事件都不发（维持原有的快路径）。"""
    cap = []
    orch = _exam_orch((True,), cap)
    events = [ev async for ev in orch.run("B", verify=False, force_simple=True)]
    assert not [e for e in events if isinstance(e, Progress) and e.scope == "verify"]
    assert len(cap) == 1


async def test_exam_turn_failed_review_redoes_without_exam_tools():
    """校验不过 → 清屏 + 就地重答一次；重答给空工具表。

    重答留着工具，模型可能再调一次 start_exam 把考试进度整个重置——这正是当初
    整条关掉校验的理由。考试轮的原料（判定结论/正确答案/解析/下一题题面）都已在
    上下文里，重答只是重新组织文字，不需要任何工具。
    """
    cap = []
    orch = _exam_orch((False, True), cap)
    base_reg = ToolRegistry()
    events = [ev async for ev in orch.run("B", verify=True, force_simple=True,
                                          registry=base_reg)]
    scopes = [(e.scope, e.text) for e in events if isinstance(e, Progress)]
    assert ("verify", "补一下X") in scopes             # 未过的原因透给前端
    assert ("reset", "") in scopes                     # 清屏，避免两版拼接
    assert len(cap) == 2                               # 重答了一次
    assert cap[0]["registry"] is base_reg              # 第一版：正常工具表
    assert cap[1]["registry"].tools() == []            # 重答：空工具表，碰不到 start_exam
    assert "结果校验" in cap[1]["msg"]                  # 重答指令带上了 Critic 的意见
    assert events[-1].message.content == "第2版讲解"    # 交付的是重答那版


async def test_exam_turn_first_run_finished_suppressed_until_review():
    """第一版的 RunFinished 必须压到校验有结论之后才发。

    它是终结信号，提前发出去前端立刻标「已完成」，而这一版随时可能被重答顶掉。
    """
    cap = []
    orch = _exam_orch((False, True), cap)
    events = [ev async for ev in orch.run("B", verify=True, force_simple=True)]
    assert len([e for e in events if isinstance(e, RunFinished)]) == 1
    # 校验事件必须早于唯一那条 RunFinished
    vi = next(i for i, e in enumerate(events)
              if isinstance(e, Progress) and e.scope == "verify")
    fi = next(i for i, e in enumerate(events) if isinstance(e, RunFinished))
    assert vi < fi


async def test_plain_simple_turn_still_skips_review():
    """非考试的简单轮维持原样：不校验。

    校验寒暄没有意义，且每轮多一次主模型往返会拖慢最快的那条路径。
    """
    cap = []
    orch = _exam_orch((True,), cap)
    events = [ev async for ev in orch.run("你好", verify=True)]   # 命中 _obvious_simple
    assert not [e for e in events if isinstance(e, Progress) and e.scope == "verify"]
    assert len(cap) == 1


# ---- 技能路由的关闭条件：只在真正逐题作答时关，不跟 force_simple 一起关 ----

def _skill_orch(order=None):
    from types import SimpleNamespace
    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(), order if order is not None else [],
               triage_simple=True)
    orch._skill_matcher = SimpleNamespace(match=lambda msg: SimpleNamespace(
        name="错题精讲", description="精讲错题并举一反三", body="剧本正文"))
    return orch


async def test_skill_routing_survives_force_simple():
    """force_simple 不该关掉技能路由。

    覆盖 Bug：「讲讲我的错题」含考试触发词「错题」→ chat 路由置 force_simple → 技能路由
    被整段跳过，而错题精讲技能的触发词恰恰就是这些词，等于被自己的触发词挡在门外。
    force_simple 宽是对的（「考我10道题」这类开考请求也得走单循环），但技能剧本只在
    **正在逐题作答**时才会干扰推进。
    """
    orch = _skill_orch()
    events = [ev async for ev in orch.run("讲讲我的错题", force_simple=True)]
    skill_evs = [e for e in events if isinstance(e, Progress) and e.scope == "skill"]
    assert skill_evs, "force_simple 下技能仍应命中"
    assert "错题精讲" in skill_evs[0].text


async def test_skill_routing_off_while_answering_questions():
    """正在逐题作答（in_stateful_exam）时才关技能路由——剧本会打乱逐题推进。"""
    orch = _skill_orch()
    events = [ev async for ev in orch.run("B", force_simple=True, in_stateful_exam=True)]
    assert not [e for e in events if isinstance(e, Progress) and e.scope == "skill"]


async def test_skill_hint_reaches_simple_answer_under_force_simple():
    """命中的剧本要真的喂进简单直答，否则「命中」只是发了个事件、不影响作答。"""
    from harness.types import Message, Role
    seen = {}

    async def cap_simple(msg, budget=None, *, context=None, registry=None,
                         prefer_main=False, skill_hint=""):
        seen["skill_hint"] = skill_hint
        yield RunFinished(message=Message(role=Role.ASSISTANT, content="答复"))

    orch = _skill_orch()
    orch._simple_answer = cap_simple
    [ev async for ev in orch.run("讲讲我的错题", verify=False, force_simple=True)]
    assert seen["skill_hint"] == "剧本正文"


# ---------- triage 必须看得见上下文 ----------

def _triage_orch(reply: str):
    """只装 triage 需要的那点依赖，直接测 _is_simple 本身（不是替身）。"""
    seen = {}

    async def _fc(system, user):
        seen["system"], seen["user"] = system, user
        return reply
    o = Orchestrator.__new__(Orchestrator)
    o._fast_complete = _fc
    return o, seen


async def test_triage_receives_recent_dialogue():
    """回归：triage 只看孤立的一句话，多步任务的追问被判成简单。

    后果不只是显示忽合忽分（计划树 vs 计划块+独立工具块）——它真的跳过了规划，
    该拆成多步的活儿退化成一轮 ReAct。
    """
    o, seen = _triage_orch("complex")
    await o._is_simple("再详细点", "用户：帮我调研 AI 现状并整理成笔记\n助手：已完成三步…")
    assert "再详细点" in seen["user"]
    assert "帮我调研" in seen["user"], "最近对话必须进 triage 的输入"
    assert "最近对话" in seen["user"] and "本次消息" in seen["user"]   # 两段要能分辨


async def test_triage_prompt_tells_model_followups_are_complex():
    """光把对话塞进去不够：得明说「承接前文任务的追问算复杂」，否则短句照样判 simple。"""
    from app.orchestration.orchestrator import TRIAGE_SYSTEM
    assert "追问" in TRIAGE_SYSTEM and "复杂" in TRIAGE_SYSTEM


async def test_triage_without_dialogue_keeps_bare_message():
    """首轮没有上下文时保持原样，不给 triage 塞空壳标签。"""
    o, seen = _triage_orch("simple")
    assert await o._is_simple("你好") is True
    assert seen["user"] == "你好"


async def test_triage_dialogue_is_capped_and_keeps_tail():
    """对话截断保尾：越近的轮次越能说明当前意图，且 triage 每轮都跑，不能无界增长。"""
    from app.orchestration.orchestrator import _TRIAGE_DIALOGUE_MAX
    o, seen = _triage_orch("complex")
    long_dialogue = "【最早这轮】" + "中间无关内容。" * 500 + "【最近这轮】帮我调研 AI 现状"
    await o._is_simple("继续", long_dialogue)
    assert "【最近这轮】帮我调研 AI 现状" in seen["user"], "必须保住最近的轮次"
    assert "【最早这轮】" not in seen["user"], "超限部分应从头部截掉"
    assert len(seen["user"]) < _TRIAGE_DIALOGUE_MAX + 200


async def test_triage_failure_still_falls_back_to_full_orchestration():
    """triage 调用失败时仍走完整编排（宁可多做不可少做），不因新增参数改变。"""
    o = Orchestrator.__new__(Orchestrator)

    async def _boom(system, user):
        raise RuntimeError("端点抖动")
    o._fast_complete = _boom
    assert await o._is_simple("继续", "前文…") is False


# ---------- 考试轮的终局校验不得把「逐题呈现」判成内容遗漏 ----------

async def test_exam_review_gets_one_question_at_a_time_rule():
    """回归：用户实际遇到的误判——

    「未通过 — 目标要求抽取5道题进行考试，但当前产出仅提供了第1题，缺失了第2至第5题的
    内容。这构成了实质性的内容遗漏」。而逐题呈现恰恰是对的：服务端托管游标，模型本轮
    只负责讲解上一题 + 呈现当前这一道。

    误判的代价不对称：它触发整轮重答，用户白等一次，重答出来的还是同一道题。
    """
    seen = {}

    class _CapturingCritic:
        async def review(self, goal, plan, artifacts, recent_dialogue=""):
            seen["goal"] = goal
            from app.orchestration.plan import Review
            return Review(accept=True, feedback="")

        async def validate(self, step, artifact):
            from app.orchestration.plan import Verdict
            return Verdict(ok=True, reason="")

    orch = _mk(FakePlanner([_plan(_s("s1"))]), _CapturingCritic(), [])

    from harness.events import TextDelta
    from harness.types import Message, Role

    async def _draft(msg, budget=None, *, context=None, registry=None,
                     prefer_main=False, skill_hint=""):
        yield TextDelta(text="第1题：……")        # draft 从 TextDelta 累加，空产出会提前返回
        yield RunFinished(message=Message(role=Role.ASSISTANT, content="第1题：……"))
    orch._simple_answer = _draft

    [e async for e in orch._simple_answer_verified("从题库抽5道题考考我")]
    g = seen["goal"]
    assert "一次只出一道" in g, "必须告诉校验器考试的推进规则"
    assert "不要因为" in g and "内容遗漏" in g, "要点名这个具体误判"
    assert "从题库抽5道题考考我" in g, "用户原始请求不能被说明挤掉"


async def test_exam_review_still_states_what_to_actually_check():
    """别做成「考试轮一律放行」——判分说反、漏告知错题集、篡改题目仍要拦。"""
    from app.orchestration.orchestrator import _EXAM_REVIEW_NOTE
    assert "说反" in _EXAM_REVIEW_NOTE
    assert "错题集" in _EXAM_REVIEW_NOTE
    assert "篡改" in _EXAM_REVIEW_NOTE


async def test_exam_review_forbids_mismatched_analysis_misjudgment():
    """回归：用户实际遇到的第二种误判——

    一轮回复=「讲解上一题(CompletableFuture) + 呈现新题(类加载机制)」是标准结构，
    但校验器把上一题的讲解误当成新题的解析，判「张冠李戴/幻觉/复制粘贴」，还反过来
    指控模型没解析当前题。note 必须明确封死这个推理：讲解针对上一题、与新题本就不同
    主题；当前新题只原样呈现、本轮不该有解析。
    """
    from app.orchestration.orchestrator import _EXAM_REVIEW_NOTE
    n = _EXAM_REVIEW_NOTE
    assert "上一题" in n, "要点明讲解针对的是上一题"
    assert "张冠李戴" in n, "要点名这个具体误判"
    assert "原样呈现" in n, "要说清当前新题本轮只呈现、不解析"


async def test_non_exam_round_review_has_no_exam_note():
    """反向：多步任务的终局 review 不该被塞考试说明，否则真正的内容遗漏会被放过。"""
    seen = {}

    class _CapturingCritic:
        async def review(self, goal, plan, artifacts, recent_dialogue=""):
            seen["goal"] = goal
            from app.orchestration.plan import Review
            return Review(accept=True, feedback="")

        async def validate(self, step, artifact):
            from app.orchestration.plan import Verdict
            return Verdict(ok=True, reason="")

    orch = _mk(FakePlanner([_plan(_s("s1"))]), _CapturingCritic(), [], triage_simple=False)
    await _run(orch, "调研 AI 现状并整理成报告")
    assert "一次只出一道" not in seen.get("goal", "")


async def test_triage_prompt_treats_pasted_material_as_simple():
    """「翻译这段：<长日志>」不该走编排器：原文已在消息里，不需拆步骤也不需工具。

    长度不是复杂度。判成复杂会绕远路，而在子步拿不到原文时（见 executor 的 goal 修复前）
    还会直接失败成「请提供文本」。
    """
    from app.orchestration.orchestrator import TRIAGE_SYSTEM
    assert "贴在消息里" in TRIAGE_SYSTEM
    assert "长度不是复杂度" in TRIAGE_SYSTEM


async def test_simple_path_verifies_when_switch_on():
    """回归：开了结果校验，简单直答却完全不校验。

    此前只有考试轮走 _simple_answer_verified，理由是「校验寒暄没意义」。但简单路径如今
    也承接实质任务（翻译/总结这一段），那些是真交付物，开关在这条路上等于形同虚设。
    """
    import app.orchestration.orchestrator as om
    seen = {}

    async def _verified(self, message, budget=None, *, context=None, registry=None,
                        skill_hint="", in_exam=True, recent_dialogue=""):
        seen["called"] = True
        seen["in_exam"] = in_exam
        seen["recent_dialogue"] = recent_dialogue
        from harness.events import RunFinished
        from harness.types import Message, Role
        yield RunFinished(message=Message(role=Role.ASSISTANT, content="答"))

    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(), [], triage_simple=True)
    orch._simple_answer_verified = _verified.__get__(orch, om.Orchestrator)
    await _run(orch, "翻译这段：Node 20 is being deprecated")
    assert seen.get("called"), "开了结果校验的实质任务必须走带校验的直答"
    assert seen["in_exam"] is False, "非考试轮不该套用考试审查说明"


async def test_greeting_still_skips_verification():
    """反向：纯寒暄仍不校验——校验「你好」纯属白烧一次往返。"""
    import app.orchestration.orchestrator as om
    seen = {}

    async def _verified(self, message, budget=None, *, context=None, registry=None,
                        skill_hint="", in_exam=True):
        seen["called"] = True
        from harness.events import RunFinished
        from harness.types import Message, Role
        yield RunFinished(message=Message(role=Role.ASSISTANT, content="答"))

    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(), [], triage_simple=True)
    orch._simple_answer_verified = _verified.__get__(orch, om.Orchestrator)
    await _run(orch, "你好")
    assert not seen.get("called")


def _routes(events):
    return [e for e in events if isinstance(e, Progress) and e.scope == "route"]


async def test_simple_path_announces_route():
    """简单直答不出「任务步骤」块，用户此前只能靠「没有块」反推走了哪条路。"""
    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(), [], triage_simple=True)
    rs = _routes(await _run(orch, "你好"))
    assert len(rs) == 1
    assert rs[0].detail == {"mode": "simple"}
    assert rs[0].text == "简单直答"


async def test_planning_path_announces_route_before_planning():
    """徽章须先于规划发出：等计划出来才追认，用户在最慢的那条路上先看到的是空白。"""
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2"))]), FakeCritic(), order)
    events = await _run(orch, "调研 AI 现状并整理成报告")
    rs = _routes(events)
    assert len(rs) == 1
    assert rs[0].detail == {"mode": "plan"}
    plans = [i for i, e in enumerate(events)
             if isinstance(e, Progress) and e.scope in ("plan", "plan_reasoning")]
    assert plans and events.index(rs[0]) < plans[0], "路由徽章必须早于任何规划事件"


async def test_planner_error_fallback_reports_simple_route():
    """规划失败降级到单循环 → 报「简单直答」。

    徽章按用户**实际收到**的呈现：规划失败后他拿到的就是一份简单答复，故报 simple。
    （这与「1 步计划回退简单直答」同一原则：徽章推迟到确定走向后才发，反映真实交付方式，
    而非中途一度尝试过的路径。）
    """
    orch = _mk(FakePlanner([_plan(_s("s1"))], raise_on_plan=True), FakeCritic(), [])
    rs = _routes(await _run(orch, "调研 AI 现状并整理成报告"))
    assert [r.detail["mode"] for r in rs] == ["simple"]


# ---------- 单步重试时清理上一次尝试的副作用 ----------

def _fx(download=(), knowledge=(), questions=()):
    return {"download": list(download), "knowledge": list(knowledge),
            "questions": list(questions)}


class _RecordingPurger:
    """冒充装配层注入的清理回调，记录每次被要求清理什么。"""
    def __init__(self, purged=("d1",)):
        self.calls = []
        self._purged = list(purged)

    def __call__(self, fx):
        self.calls.append(fx)
        return list(self._purged)


class _FxExecutor:
    """按次序产出带副作用清单的 StepArtifact。"""
    def __init__(self, fxs, terminal=False):
        self._fxs = list(fxs); self._i = 0

    async def execute(self, step, deps, hint="", *, registry=None, goal="", fx_sink=None, **_kw):
        from app.orchestration.executor import StepArtifact
        fx, terminal = self._fxs[min(self._i, len(self._fxs) - 1)]
        self._i += 1
        yield Progress(f"subagent:executor:{step.id}", "开始")
        yield StepArtifact(Artifact(summary=f"done-{step.id}"),
                           side_effects=fx, terminal=terminal)


class _SeqCritic:
    """validate 按次序给结论，review 一律通过。"""
    def __init__(self, verdicts):
        self._v = list(verdicts); self._i = 0

    async def validate(self, step, artifact):
        ok = self._v[min(self._i, len(self._v) - 1)]; self._i += 1
        return Verdict(ok=ok, reason="不够好")

    async def review(self, goal, plan, artifacts, recent_dialogue=""):
        return Review(accept=True, feedback="")


def _fx_orch(fxs, verdicts):
    # 两步（s2 依赖 s1）：1 步计划现在会回退简单直答、不进编排器，这些 purge/reset 测试要真的
    # 走编排器。s1 终态失败时 s2 被 skip（不产生多余 purge）；s1 成功时 s2 在下批跑，序列尾项
    # 对这些用例均良性（空 fx / 通过），不新增 purge/reset。
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2", deps=["s1"]))]), _SeqCritic(verdicts), [])
    orch._executor = _FxExecutor(fxs)
    return orch


async def test_retry_purges_previous_attempt_side_effects():
    """单步校验不过 → 重跑前先删掉这次尝试留下的产物。

    不删的话模型重跑会把 save_download 再调一遍，聊天下方挂出两个下载按钮，
    其中一个还是被判为不合格的那一版。
    """
    purger = _RecordingPurger()
    orch = _fx_orch([(_fx(download=["d1"]), False), (_fx(), False)], [False, True])
    [ev async for ev in orch.run("做点事", purge_side_effects=purger)]
    assert purger.calls == [_fx(download=["d1"])]      # 只清失败那次的产物


async def test_no_purge_when_step_passes_first_try():
    purger = _RecordingPurger()
    orch = _fx_orch([(_fx(download=["d1"]), False)], [True])
    [ev async for ev in orch.run("做点事", purge_side_effects=purger)]
    assert purger.calls == []                          # 通过就不该动产物


async def test_no_purge_when_attempt_produced_nothing():
    """这次尝试没产出产物就别去调清理——省一次无谓的 store 往返。"""
    purger = _RecordingPurger()
    orch = _fx_orch([(_fx(), False), (_fx(), False)], [False, True])
    [ev async for ev in orch.run("做点事", purge_side_effects=purger)]
    assert purger.calls == []


async def test_purge_emits_progress_so_frontend_drops_buttons():
    """删了产物必须告诉在途前端，否则已渲染的下载按钮还在，点开是已删的文件。"""
    purger = _RecordingPurger(purged=["d1"])
    orch = _fx_orch([(_fx(download=["d1"]), False), (_fx(), False)], [False, True])
    evs = [ev async for ev in orch.run("做点事", purge_side_effects=purger)]
    purged = [e for e in evs if isinstance(e, Progress) and e.scope == "purged"]
    assert purged and "d1" in purged[0].text


async def test_retry_emits_step_reset_to_clear_tool_log():
    """重跑前让前端抖掉这一步上次的工具调用记录，不然同一步里同一个工具显示调了两遍。"""
    purger = _RecordingPurger()
    orch = _fx_orch([(_fx(), False), (_fx(), False)], [False, True])
    evs = [ev async for ev in orch.run("做点事", purge_side_effects=purger)]
    resets = [e for e in evs if isinstance(e, Progress) and e.scope == "step_reset"]
    assert resets and resets[0].text == "s1"


async def test_terminal_failure_still_purges():
    """用户拒绝危险操作 → 不重试，但这一版已产出的东西同样作废，照样要清。"""
    purger = _RecordingPurger()
    orch = _fx_orch([(_fx(download=["d1"]), True)], [False])
    [ev async for ev in orch.run("做点事", purge_side_effects=purger)]
    assert purger.calls == [_fx(download=["d1"])]


async def test_runs_without_purger_injected():
    """未注入清理回调（精简装配/测试）时照常跑完，不报错。"""
    orch = _fx_orch([(_fx(download=["d1"]), False), (_fx(), False)], [False, True])
    [ev async for ev in orch.run("做点事")]


async def test_no_step_reset_when_step_wont_rerun():
    """终态失败/重试耗尽 → 该步不会重跑，就不能抖掉它的工具记录。

    抖掉等于把失败现场一并抹了：这一步到底调了什么、错在哪，用户再也看不到。
    step_reset 的语义是「旧记录马上会被新一轮取代」，不重跑就不成立。
    产物照样要清——它已经作废了，与重不重跑无关。
    """
    purger = _RecordingPurger()
    orch = _fx_orch([(_fx(download=["d1"]), True)], [False])   # terminal=True → 判 failed
    evs = [ev async for ev in orch.run("做点事", purge_side_effects=purger)]
    assert purger.calls == [_fx(download=["d1"])]              # 产物仍清
    resets = [e for e in evs if isinstance(e, Progress) and e.scope == "step_reset"]
    assert resets == [], "不重跑的步不该抖掉工具记录"


async def test_no_step_reset_after_retries_exhausted():
    """重试次数用尽（max_step_retry=2）判 failed 的那次，同样不发 step_reset。"""
    purger = _RecordingPurger()
    orch = _fx_orch([(_fx(), False)], [False, False])          # 两次都不过 → 第二次判 failed
    evs = [ev async for ev in orch.run("做点事", purge_side_effects=purger)]
    resets = [e for e in evs if isinstance(e, Progress) and e.scope == "step_reset"]
    assert len(resets) == 1, "只有第一次（会重跑）该发，最后一次判 failed 不该发"


async def test_purges_even_when_step_crashes_after_producing():
    """本步异常逃出 AgentLoop 兜底时，已落盘的产物照样要清。

    这条路上 StepArtifact 根本 yield 不出来，产物清单只能靠传给 executor 的 sink 拿到。
    """
    class _CrashAfterSave:
        async def execute(self, step, deps, hint="", *, registry=None, goal="", fx_sink=None, **_kw):
            if fx_sink is not None:
                fx_sink.update({"download": ["d1"], "knowledge": [], "questions": []})
            yield Progress(f"subagent:executor:{step.id}", "调用工具 save_download", status="ok")
            raise RuntimeError("连接断了")

    purger = _RecordingPurger()
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2", deps=["s1"]))]), _SeqCritic([True]), [])
    orch._executor = _CrashAfterSave()
    [ev async for ev in orch.run("做点事", purge_side_effects=purger)]
    # 崩了会重试，重试又崩 —— 两次尝试各自的产物都要清，故调用 2 次
    assert purger.calls == [{"download": ["d1"], "knowledge": [], "questions": []}] * 2


async def test_purged_payload_is_a_flat_id_array():
    """purged 的载荷必须是**扁平 id 数组**，前端 Array.isArray 不过就静默丢弃。

    这条容易踩：SideEffectPurger.purge 返回的是按类分组的 dict，而本回调约定返回 list。
    两者名字相近、都叫"清理结果"，直接把 purger 接上来就会发出 {"download": [...]}，
    前端 JSON.parse 得到对象 → Array.isArray 为假 → 直接 return，按钮永远撤不掉，
    且不报任何错。只断言"文本里含 d1"是抓不住的——dict 的 JSON 里同样含 d1。
    """
    orch = _fx_orch([(_fx(download=["d1"]), False), (_fx(), False)], [False, True])
    evs = [ev async for ev in orch.run(
        "做点事", purge_side_effects=lambda fx: ["d1", "d2"])]
    payload = [e for e in evs if isinstance(e, Progress) and e.scope == "purged"][0].text
    assert json.loads(payload) == ["d1", "d2"]


async def test_purged_payload_normalized_when_callback_returns_grouped_dict():
    """回调若返回 purge() 那种分组 dict，也要归一成下载 id 数组再发，不能原样丢给前端。"""
    orch = _fx_orch([(_fx(download=["d1"]), False), (_fx(), False)], [False, True])
    evs = [ev async for ev in orch.run("做点事", purge_side_effects=lambda fx: {
        "download": ["d1"], "knowledge": ["k1"], "questions": []})]
    payload = [e for e in evs if isinstance(e, Progress) and e.scope == "purged"][0].text
    assert json.loads(payload) == ["d1"]


async def test_no_purged_event_when_nothing_actually_deleted():
    """删除全失败时不发 purged：前端据它标注"已作废删除"，而文件其实还在。"""
    orch = _fx_orch([(_fx(download=["d1"]), False), (_fx(), False)], [False, True])
    evs = [ev async for ev in orch.run("做点事", purge_side_effects=lambda fx: [])]
    assert [e for e in evs if isinstance(e, Progress) and e.scope == "purged"] == []
# ---- save_download 按步下发（防「生成内容」步顺手存一份，与「保存文件」步各存一份）----

def _fake_tool(name):
    class T:
        pass
    t = T(); t.name = name
    return t


class _Reg:
    """最小 ToolRegistry 替身：只需支撑 HidingRegistry 的 get/tools。"""
    def __init__(self, names):
        self._t = [_fake_tool(n) for n in names]

    def get(self, name):
        return next((t for t in self._t if t.name == name), None)

    def tools(self):
        return list(self._t)


def _names(reg):
    return {t.name for t in reg.tools()} if reg is not None else set()


async def _run_capturing_regs(plan, goal="把内容整理好并存成可下载文件"):
    """跑一轮，记下每个步骤实际拿到的工具表。"""
    seen: dict[str, set] = {}
    order = []

    class RecordingExecutor(FakeExecutor):
        async def execute(self, step, deps, hint="", *, registry=None, goal="", done_effects=None, **_kw):
            seen[step.id] = _names(registry)
            async for ev in super().execute(step, deps, hint, registry=registry, goal=goal,
                                            done_effects=done_effects):
                yield ev

    orch = _mk(FakePlanner([plan]), FakeCritic(), order)
    orch._executor = RecordingExecutor(order)
    base = _Reg(["save_download", "web_search", "calculator"])
    async for _ in orch._schedule_rounds(plan, {}, None, base, goal=goal):
        pass
    return seen


async def test_save_download_only_reaches_the_file_producing_step():
    """「生成内容」步不该拿到 save_download——它校验没过被要求重试时就会抓这个工具用上，
    后面真正的保存步再存一次，下载区两份重复文件（实测症状）。"""
    plan = _plan(
        PlanStep(id="s1", description="撰写一份关于AI的学习笔记内容", expected="完整的笔记正文"),
        PlanStep(id="s2", description="将笔记保存为可下载的文件", expected="可下载的 .md 文件",
                 depends_on=["s1"]))
    seen = await _run_capturing_regs(plan)
    assert "save_download" not in seen["s1"], "生成内容的步骤不该看得见存文件的工具"
    assert "save_download" in seen["s2"], "真正要产出文件的那步必须拿得到"
    # 其余工具一个都不能少：这里只收 save_download，不是给子步换一份阉割工具表
    assert {"web_search", "calculator"} <= seen["s1"]


async def test_all_steps_keep_save_download_when_no_step_looks_like_saving():
    """一步都不像要产文件时不收紧——正则漏判把该存的那步也堵死，比重复保存严重得多
    （用户什么都拿不到）。宁可退回原行为。"""
    plan = _plan(PlanStep(id="s1", description="调研AI现状", expected="调研结论"),
                 PlanStep(id="s2", description="归纳要点", expected="要点清单", depends_on=["s1"]))
    seen = await _run_capturing_regs(plan)
    assert all("save_download" in v for v in seen.values())


async def test_multiple_file_steps_all_get_the_tool():
    """用户要两份不同文件时，两步都得拿得到——收紧的是「非产出文件的步」，不是「只留一步」。"""
    plan = _plan(
        PlanStep(id="s1", description="生成大纲并导出为文件", expected="大纲文件"),
        PlanStep(id="s2", description="生成正文并存成可下载文件", expected="正文文件"),
        PlanStep(id="s3", description="总结要点", expected="要点"))
    seen = await _run_capturing_regs(plan)
    assert "save_download" in seen["s1"] and "save_download" in seen["s2"]
    assert "save_download" not in seen["s3"]


# ---- 重试时告知「上次已经做过的带副作用调用」----

async def test_retry_prompt_carries_previous_side_effects():
    """重试的 prompt 此前只有质检意见，没有「上次已经做过什么」，于是带副作用的工具被原样
    重来——同一份笔记存两次即由此而来。"""
    prompts = []

    class ReExecutor:
        def __init__(self, order):
            self._n = 0

        async def execute(self, step, deps, hint="", *, registry=None, goal="",
                          done_effects=None, **_kw):
            from app.orchestration.executor import StepArtifact, _build_prompt
            prompts.append(_build_prompt(step, deps, hint, goal, done_effects))
            self._n += 1
            # 第一次：调过 save_download 并留下产物；第二次：什么也没再调
            eff = ["save_download"] if self._n == 1 else []
            merged = list(done_effects or []) + [e for e in eff if e not in (done_effects or [])]
            yield StepArtifact(Artifact(summary=f"稿子v{self._n}"), effects=merged)

    orch = _mk(FakePlanner([_plan(_s("s1"))]),
               FakeCritic(validate_ok=False, reviews=(True,)), [])
    orch._executor = ReExecutor([])
    async for _ in orch._schedule_rounds(_plan(_s("s1")), {}, None, None, goal="g"):
        pass
    assert len(prompts) == 2, "该重试一次"
    assert "save_download" not in prompts[0], "首次尝试不该凭空提到工具"
    assert "save_download" in prompts[1], "重试必须告知上次已经存过文件"
    assert "不要再调用一次" in prompts[1], "语气要是「别再做」，中性陈述模型会照样再调"


async def test_side_effects_survive_a_retry_that_calls_nothing():
    """第 2 次没再调，不代表第 1 次的产物消失了——第 3 次仍须被告知。

    真跑三次尝试（max_step_retry 调到 3），中间那次不调任何工具，看第三次的 prompt。
    """
    prompts = []

    class ReExecutor:
        def __init__(self, order):
            self._n = 0

        async def execute(self, step, deps, hint="", *, registry=None, goal="",
                          done_effects=None, **_kw):
            from app.orchestration.executor import StepArtifact, _build_prompt
            prompts.append(_build_prompt(step, deps, hint, goal, done_effects))
            self._n += 1
            eff = ["save_download"] if self._n == 1 else []   # 只有第一次调了
            merged = list(done_effects or [])
            merged += [e for e in eff if e not in merged]
            yield StepArtifact(Artifact(summary=f"稿子v{self._n}"), effects=merged)

    orch = _mk(FakePlanner([_plan(_s("s1"))]),
               FakeCritic(validate_ok=False, reviews=(True,)), [], max_replan=0)
    orch._executor = ReExecutor([])
    orch._max_step_retry = 3
    async for _ in orch._schedule_rounds(_plan(_s("s1")), {}, None, None, goal="g"):
        pass
    assert len(prompts) == 3, f"该跑三次，实际 {len(prompts)}"
    assert "save_download" not in prompts[0]
    assert "save_download" in prompts[1], "第二次须知道第一次存过"
    assert "save_download" in prompts[2], "第二次没再调，但第一次的产物还在，第三次仍须知道"


def test_effects_note_absent_without_effects():
    """没有副作用就不该平白多出一段告诫——那会让模型以为自己做过什么。"""
    from app.orchestration.executor import _build_prompt
    for eff in (None, []):
        got = _build_prompt(_s("s1"), {}, "改进一下", "g", eff)
        assert "不要再调用一次" not in got


# ---- 按步下发的两道安全性质：误判只许退化，不许堵死交付 ----

def _p(*rows):
    return Plan(goal="g", steps=[PlanStep(id=i, description=d, expected=e, depends_on=list(dep))
                                 for i, d, e, dep in rows])


def test_terminal_steps_kept_when_no_terminal_looks_like_a_saver():
    """命中的全是中间步时，把终端步一并放行——交付物必然出自终端步。

    没有这道闸，假阳性会**反向放大**：某个非交付步被误判成产文件 → savers 非空 → 就此
    打开了对其他步的限制 → 不该存的拿到工具、该存的反被堵死，方向正好搞反。
    有了它，误判最坏只是「多给一个用不上的工具」。
    反之只要已有终端步认领了交付就不放行别人，否则「三个互不依赖的步全是终端步」会让限制失效
    （见 test_multiple_file_steps_all_get_the_tool）。
    """
    from app.orchestration.plan import file_saving_step_ids
    # s1 被正则误判（讲的是文件格式，并不产出文件），s2 才是真交付
    plan = _p(("s1", "讲解如何生成配置文件", "讲解文本", ()),
              ("s2", "把讲解整理后交给用户", "最终答复", ("s1",)))
    assert "s2" in file_saving_step_ids(plan)


def test_bare_extension_no_longer_triggers_restriction():
    """裸扩展名是最弱的信号：「解析上传的 .csv 数据」只是提到文件，并不产出文件，
    却足以把整个计划翻进限制模式。真要存文件的说法本就被「存…文件」匹配到，不缺这条。"""
    from app.orchestration.plan import file_saving_step_ids
    plan = _p(("s1", "解析上传的 .csv 数据并提取关键指标", "关键指标列表", ()),
              ("s2", "生成一份分析报告", "分析报告", ("s1",)))
    assert file_saving_step_ids(plan) is None, "不该因为提到 .csv 就开启限制"


def test_reported_bug_still_fixed_with_the_new_guards():
    """回归：两道安全闸都加上后，原 bug 仍须修住——
    s1 有后继、非终端、描述不像产文件 → 拿不到 save_download。"""
    from app.orchestration.plan import file_saving_step_ids
    plan = _p(("s1", "撰写一份关于AI的学习笔记内容", "完整的笔记正文", ()),
              ("s2", "将笔记保存为可下载的文件", "可下载的文件", ("s1",)))
    savers = file_saving_step_ids(plan)
    assert savers == {"s2"}


def test_save_file_regex_covers_common_phrasings_without_false_positives():
    """正则的正反例都钉住：假阳性会反向放大（见 file_saving_step_ids），
    假阴性会让该拿工具的步拿不到。两边都得管。"""
    from app.orchestration.plan import _SAVE_FILE_RE as R
    hit = ["将内容保存为可下载的文件", "把整理好的笔记存成 .md 文件", "调用 save_download 保存",
           "输出可下载成品", "导出为文件供用户下载",
           # 以下曾漏判
           "把结果落盘", "整理成讲义并提供给用户下载", "供用户下载的成品",
           # 工具描述里专门交代了「用户即使说导出 PDF 也要存成 .md」，说明这是预期会出现的说法
           "导出为 PDF", "生成 Excel 表格交付"]
    miss = ["调研AI现状", "撰写关于AI的总结", "总结要点", "生成图表数据",
            # 以下曾误判：只是提到文件，并不产出文件
            "解析上传的 .csv 数据并提取关键指标", "检查 config.json 的格式是否正确",
            "阅读 README.md 了解项目结构"]
    assert [t for t in hit if not R.search(t)] == []
    assert [t for t in miss if R.search(t)] == []


def test_description_and_expected_matched_separately_not_concatenated():
    """两字段分别匹配：拼成一串时间隔类会吃掉空格，于是「描述末尾 + 预期开头」跨界命中，
    而两边各自都无害——「整理要点并输出」+「文件名清单」被判成产文件步，触发反向放大。"""
    from app.orchestration.plan import file_saving_step_ids
    plan = _p(("s1", "整理要点并输出", "文件名清单", ()),
              ("s2", "列出需要生成的内容", "文件格式说明", ("s1",)))
    assert file_saving_step_ids(plan) is None


def test_effects_note_allows_resaving_when_content_actually_changed():
    """告诫语不能一刀切说「不要再调用」：本步若本职就是产出文件，质检不通过说的往往正是
    产物内容不行；改好了却不许再存，下载区就永久留着被否的那一版，而 Critic 只看 summary
    会判它通过——一个 bug 换成另一个更隐蔽的。"""
    from app.orchestration.executor import _build_prompt
    got = _build_prompt(_s("s1"), {}, "第二章太浅，请补充", "g", ["save_download"])
    assert "还在，没有丢失" in got                      # 既成事实要说清
    assert "不要再调用一次" in got                      # 内容没变时仍须拦住重复保存
    assert "真的改动了产物内容时，才照常再调一次" in got  # 内容变了则本该再存


def test_non_terminal_step_never_saves_even_when_its_expected_mentions_files():
    """回归（用户实测「生成一首诗，保存到下载」仍重复保存）：

    planner 写 expected 时会前瞻性交代下游用途——「一首完整的诗，供后续保存为文件」——
    于是「创作」这一步照样命中正则、拿到 save_download，和后面真正的保存步各存一份。
    措辞判断在这里必然漏，因为交叉引用是 planner 的正常写法。改用结构判据：
    被其他步骤依赖的步，产出的是下游的输入，不是给用户的交付物。
    """
    from app.orchestration.plan import file_saving_step_ids
    for expected in ("一首完整的诗，供后续保存为文件", "诗歌正文，用于生成可下载文件"):
        plan = _p(("s1", "创作一首诗", expected, ()),
                  ("s2", "将创作的诗保存为可下载的文件", "可下载的文件", ("s1",)))
        assert file_saving_step_ids(plan) == {"s2"}, f"expected={expected!r} 时 s1 漏了"


def test_middle_step_still_allowed_when_no_terminal_step_claims_delivery():
    """反向：没有任何终端步像是要存文件时，命中的中间步仍须放行——
    此时多半是识别错了或交付确实发生在中间步，堵死谁都可能让用户拿不到文件。"""
    from app.orchestration.plan import file_saving_step_ids
    plan = _p(("s1", "讲解如何生成配置文件", "讲解文本", ()),
              ("s2", "把讲解整理后交给用户", "最终答复", ("s1",)))
    assert file_saving_step_ids(plan) == {"s1", "s2"}

# ---------- 两套机制的接缝：删了就得让模型重做 ----------

class _RecordingExecutor:
    """记录每次收到的 done_effects，并按剧本产出产物。"""
    def __init__(self, fxs):
        self._fxs = list(fxs); self._i = 0
        self.seen_done_effects = []
        self.seed_effect = "save_download"   # 本替身"上次调过"的那个工具

    async def execute(self, step, deps, hint="", *, registry=None, goal="",
                      done_effects=None, fx_sink=None, **_kw):
        from app.orchestration.executor import StepArtifact
        self.seen_done_effects.append(list(done_effects or []))
        fx = self._fxs[min(self._i, len(self._fxs) - 1)]; self._i += 1
        if fx_sink is not None:
            fx_sink.update(fx)
        yield Progress(f"subagent:executor:{step.id}", "调用工具 save_download", status="ok")
        # effects 累积：模型这次调了 save_download 且留下了产物
        eff = list(done_effects or [])
        if any(fx.values()) and self.seed_effect not in eff:
            eff.append(self.seed_effect)
        yield StepArtifact(Artifact(summary=f"done-{step.id}"), effects=eff, side_effects=fx)


async def test_purged_artifact_is_removed_from_done_effects_so_model_redoes_it():
    """删了产物就必须让模型重做——这是两套机制的接缝，双方各自的测试都盖不到。

    「按步下发 + 告知已做过」防的是重复保存；「重跑前清理」防的是留下不合格那版的文件。
    两者单独都对，合在一起若不协作就会：文件被删 → 模型被告知"你已经存过了" → 不再保存
    → **用户手里一个文件都没有**。故清掉某类产物后，要把对应工具名从 done_effects 摘掉。
    """
    ex = _RecordingExecutor([
        {"download": ["d1"], "knowledge": [], "questions": []},   # 首次：存了文件
        {"download": ["d2"], "knowledge": [], "questions": []},   # 重跑：重新存了
    ])
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2", deps=["s1"]))]), _SeqCritic([False, True]), [])
    orch._executor = ex
    # 回调按 SideEffectPurger.purge 的真实形状返回分组 dict
    [ev async for ev in orch.run("做点事", purge_side_effects=lambda fx: dict(fx))]

    assert len(ex.seen_done_effects) == 3, "s1 跑两次（首次+重跑）+ s2 一次；精确计数守住不多跑"
    assert ex.seen_done_effects[0] == []
    assert "save_download" not in ex.seen_done_effects[1], (
        "首次存的文件已被删，重跑时不能再告诉模型「你已经存过了」，否则它不会重存")


async def test_done_effects_kept_when_nothing_was_actually_purged():
    """删除失败（产物还在）时不能摘——再让模型存一遍就真成两份了。"""
    ex = _RecordingExecutor([
        {"download": ["d1"], "knowledge": [], "questions": []},
        {"download": [], "knowledge": [], "questions": []},
    ])
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2", deps=["s1"]))]), _SeqCritic([False, True]), [])
    orch._executor = ex
    # 清理全失败：回传各类均为空
    [ev async for ev in orch.run("做点事", purge_side_effects=lambda fx: empty_fx_for_test())]
    assert "save_download" in ex.seen_done_effects[1], "没删成就仍要告知模型别重做"


def empty_fx_for_test():
    return {"download": [], "knowledge": [], "questions": []}


async def test_sink_content_survives_an_artifact_that_forgot_side_effects():
    """只填了 sink、忘了设 StepArtifact.side_effects 的实现，产物清单不能被抹掉。

    `fx = ev.side_effects or fx` 看着像在做这个兼容，其实相反：empty_fx() 是含三个键的
    dict、恒为真，于是返回值无条件压过 sink——这种实现的产物会被默认空值整个抹掉，
    既不清理也不摘工具名。
    """
    class _SinkOnly:
        async def execute(self, step, deps, hint="", *, fx_sink=None, **_kw):
            from app.orchestration.executor import StepArtifact
            if fx_sink is not None:
                fx_sink.update({"download": ["d1"], "knowledge": [], "questions": []})
            yield Progress(f"subagent:executor:{step.id}", "存了文件", status="ok")
            yield StepArtifact(Artifact(summary="稿子"))      # 忘了带 side_effects

    purger = _RecordingPurger()
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2", deps=["s1"]))]), _SeqCritic([False, True]), [])
    orch._executor = _SinkOnly()
    [ev async for ev in orch.run("做点事", purge_side_effects=purger)]
    assert purger.calls and purger.calls[0]["download"] == ["d1"]


async def test_no_double_counting_when_sink_and_artifact_are_the_same_object():
    """真实 executor 里 sink 与 artifact.side_effects 是同一个 dict——合并时不能把 id 记两遍。"""
    class _Both:
        async def execute(self, step, deps, hint="", *, fx_sink=None, **_kw):
            from app.orchestration.executor import StepArtifact
            fx = fx_sink if fx_sink is not None else {}
            fx.update({"download": ["d1"], "knowledge": [], "questions": []})
            yield Progress(f"subagent:executor:{step.id}", "存了文件", status="ok")
            yield StepArtifact(Artifact(summary="稿子"), side_effects=fx)   # 同一对象

    purger = _RecordingPurger()
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2", deps=["s1"]))]), _SeqCritic([False, True]), [])
    orch._executor = _Both()
    [ev async for ev in orch.run("做点事", purge_side_effects=purger)]
    assert purger.calls[0]["download"] == ["d1"], "同一对象不该被记两遍"


async def test_seam_also_covers_knowledge_kind():
    """接缝此前只测过 download 一类；知识条目走同一条路，也得摘对应工具名。"""
    ex = _RecordingExecutor([
        {"download": [], "knowledge": ["k1"], "questions": []},
        {"download": [], "knowledge": ["k2"], "questions": []},
    ])
    # _RecordingExecutor 只在有 download 时补 save_download，这里手工种一个知识类的账
    ex.seed_effect = "save_to_knowledge"
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2", deps=["s1"]))]), _SeqCritic([False, True]), [])
    orch._executor = ex
    [ev async for ev in orch.run("做点事", purge_side_effects=lambda fx: dict(fx))]
    assert "save_to_knowledge" not in ex.seen_done_effects[1], (
        "知识条目被删了，重跑时不能再告诉模型「你已经存过了」")


async def test_parallel_failing_steps_prune_their_own_effects_only():
    """同一轮两个步都失败、各自带产物时，摘工具名不能串台。"""
    class _PerStep:
        def __init__(self): self.seen = {}
        async def execute(self, step, deps, hint="", *, done_effects=None, fx_sink=None, **_kw):
            from app.orchestration.executor import StepArtifact
            self.seen.setdefault(step.id, []).append(list(done_effects or []))
            # s1 存文件、s2 存知识，各自独立
            fx = ({"download": ["d1"], "knowledge": [], "questions": []} if step.id == "s1"
                  else {"download": [], "knowledge": ["k1"], "questions": []})
            if fx_sink is not None:
                fx_sink.update(fx)
            eff = list(done_effects or [])
            tool = "save_download" if step.id == "s1" else "save_to_knowledge"
            if tool not in eff:
                eff.append(tool)
            yield Progress(f"subagent:executor:{step.id}", "干活", status="ok")
            yield StepArtifact(Artifact(summary=step.id), effects=eff, side_effects=fx)

    ex = _PerStep()
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2"))]), _SeqCritic([False, False, True, True]), [])
    orch._executor = ex
    [ev async for ev in orch.run("做点事", purge_side_effects=lambda fx: dict(fx))]
    # 各自第二次尝试读到的都该是"自己那件事被撤销了"，且不含对方的工具名
    assert "save_download" not in ex.seen["s1"][1]
    assert "save_to_knowledge" not in ex.seen["s2"][1]
    assert "save_to_knowledge" not in ex.seen["s1"][1], "不该串到别的步"


async def test_non_saver_step_keeps_working_file_tools():
    """只摘 save_download，不是给子步换一份阉割工具表。

    「创作一首诗」这类中间步**可以**把内容写进沙箱工作文件（后续步骤再读出来交付），
    只是不能自己塞进用户的下载区。沙箱指引里也是这么说的：「只有当后续步骤还要在沙箱里
    读取/处理该文件时，才先 write_file」——工具视图必须和那句话对得上。
    """
    plan = _plan(
        PlanStep(id="s1", description="创作一首诗", expected="一首完整的诗，供后续保存为文件"),
        PlanStep(id="s2", description="将创作的诗保存为可下载的文件", expected="可下载的文件",
                 depends_on=["s1"]))
    seen = {}
    order = []

    class RecordingExec(FakeExecutor):
        async def execute(self, step, deps, hint="", *, registry=None, goal="",
                          done_effects=None, **kw):
            seen[step.id] = {t.name for t in registry.tools()} if registry else set()
            async for ev in super().execute(step, deps, hint, registry=registry, goal=goal,
                                            done_effects=done_effects, **kw):
                yield ev

    orch = _mk(FakePlanner([plan]), FakeCritic(), order)
    orch._executor = RecordingExec(order)
    base = _Reg(["save_download", "write_file", "read_file", "run_shell", "web_search"])
    async for _ in orch._schedule_rounds(plan, {}, None, base, goal="生成一首诗，保存到下载"):
        pass
    assert "save_download" not in seen["s1"], "创作步不该能直接塞进下载区"
    assert {"write_file", "read_file", "run_shell", "web_search"} <= seen["s1"], \
        "创作步仍须能写工作文件、跑沙箱、检索——被摘的只有 save_download 这一个"
    assert "save_download" in seen["s2"]


async def test_impossible_step_suppresses_replan():
    """结构性无法完成的步 → 不重规划。缺的工具/权限/能力本系统根本没有，重新拆一版计划
    还是撞同一堵墙。对照 test_reject_then_replan_then_accept：那里 review 拒→重规划→跑新步 s2；
    此处即便 review 拒，只要有 impossible 步，就不该重规划、新步 s2 从不执行。"""
    order = []
    planner = FakePlanner([_plan(_s("s1"), _s("s1b")), _plan(_s("s2"))])
    # s1 校验判 impossible；review 恒拒（若无 impossible 守卫，这会触发重规划跑到 s2）
    orch = _mk(planner,
               FakeCritic(validate_ok=False, validate_impossible=True, reviews=(False, False)),
               order)
    events = await _run(orch)
    assert isinstance(events[-1], RunFinished)          # 仍带残缺成果收尾
    assert order.count("s1") == 1                        # 不重试
    assert "s2" not in order, "impossible 步不该触发重规划"
    assert planner._i == 0, "replan 一次都不该发生"
    # 用户能看到「为什么没继续」：发了 verify 说明
    assert any(isinstance(e, Progress) and e.scope == "verify"
               and "无法完成" in e.text for e in events)


async def test_plain_reject_still_replans_when_not_impossible():
    """反向守卫：只是没做好（非 impossible）时，review 拒仍照常重规划——别误伤正常闭环。"""
    order = []
    planner = FakePlanner([_plan(_s("s1"), _s("s1b")), _plan(_s("s2"))])
    # validate 通过（产出没问题），但 review 先拒后受 → 正常重规划
    orch = _mk(planner, FakeCritic(validate_ok=True, reviews=(False, True)), order)
    await _run(orch)
    assert "s2" in order and planner._i == 1   # 重规划照常发生


# ---- 1 步计划回退简单直答 ----

async def test_one_step_plan_falls_back_to_simple_answer():
    """planner 只拆出 1 步 → 回退简单直答，不进编排器。走完整编排（execute→validate→
    review→synthesize）纯属把简单直答包一层昂贵仪式（尤其 synthesize 用主模型重述唯一产物）。"""
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(), order)
    events = await _run(orch, "调研 AI 现状")
    assert order == [], "1 步计划不该进编排器执行"
    # 不发任务步骤块（scope=plan 快照）——那是编排器路径的标志
    assert not any(isinstance(e, Progress) and e.scope == "plan" for e in events)
    # 徽章报「简单直答」，反映用户实际收到的呈现
    rs = _routes(events)
    assert [r.detail["mode"] for r in rs] == ["simple"]
    assert isinstance(events[-1], RunFinished)


async def test_two_step_plan_still_orchestrates():
    """对照：≥2 步仍走编排器——发多步徽章 + 任务步骤块，执行各步。"""
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2"))]), FakeCritic(), order)
    events = await _run(orch, "调研并整理成报告")
    assert order == ["s1", "s2"]
    assert any(isinstance(e, Progress) and e.scope == "plan" for e in events)
    rs = _routes(events)
    assert [r.detail["mode"] for r in rs] == ["plan"]


async def test_one_step_fallback_route_badge_after_planning_not_before():
    """回归：徽章必须推迟到规划**之后**发。若规划前就发「多步规划」、这里再回退简单直答，
    用户会先看到多步徽章再收到简单答复，自相矛盾。故 1 步回退时全程只出现 simple 徽章。"""
    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(), [])
    rs = _routes(await _run(orch, "调研 AI 现状"))
    assert [r.detail["mode"] for r in rs] == ["simple"]   # 绝不出现 plan


async def test_one_step_fallback_redo_keeps_tools_unlike_exam():
    """非考试的 1 步回退：校验拒 → 重答**带真实工具表**（与考试轮相反）。

    对照 test_exam_turn_failed_review_redoes_without_exam_tools（考试轮重答空工具、防
    start_exam 重置）：非考试的「单步但需工具」题，首答调了检索/生成，重答若无工具就只能
    空转、没法重做，是能力回退。故 in_exam=False 时重答放行工具。
    """
    cap = []
    # 复用 _exam_orch 的记录式 _simple_answer；planner 出 1 步 → run() 回退简单直答。
    # 不加 force_simple（in_exam=False），review 先拒后受触发一次重答。
    orch = _exam_orch((False, True), cap)
    base_reg = ToolRegistry()
    events = [ev async for ev in orch.run("查一下最新的 X 并总结", verify=True,
                                          registry=base_reg)]
    assert len(cap) == 2, "应重答一次"
    assert cap[0]["registry"] is base_reg              # 首答：真实工具表
    assert cap[1]["registry"] is base_reg              # 重答：仍是真实工具表（非空！）
    assert events[-1].message.content == "第2版讲解"    # 交付重答那版
    # 徽章仍报简单直答（1 步回退）
    assert [r.detail["mode"] for r in _routes(events)] == ["simple"]


# ---------- F-CHAT-1: 学习无关的复杂请求应婉拒，不进编排器拆解 ----------

def _acoro(v):
    async def _f(*a, **k):
        return v
    return _f


async def test_off_topic_complex_request_is_declined_not_planned():
    """明显无关(荐股)且被判复杂 → 直接婉拒，不调 Planner、不给工具。

    修 F-CHAT-1：编排器路径会把无关请求拆成中性子任务(「搜索股票板块」)分发执行，
    app_system_prompt 的学习边界只在单循环整体判断时生效、拦不住拆解后的子步。
    """
    planner = FakePlanner([_plan(_s("s1"))])
    orch = _mk(planner, FakeCritic(), [])
    orch._is_simple = _acoro(False)          # triage 判复杂
    orch._is_off_topic = _acoro(True)        # 判为学习无关

    captured = {}
    async def _fake_simple(message, budget=None, *, context=None, registry=None,
                           prefer_main=False, skill_hint=""):
        captured["message"] = message
        captured["no_tools"] = registry is not None and len(registry.tools()) == 0
        from harness.events import TextDelta
        yield TextDelta(text="我是学习助手，只能帮助学习相关的问题。")
    orch._simple_answer = _fake_simple

    evs = [e async for e in orch.run("帮我推荐几只能赚钱的股票")]
    assert len(planner.seen_tools) == 0, "无关请求绝不能进 Planner 拆解"
    assert captured.get("no_tools") is True, "婉拒不给工具，避免联网荐股/检索"
    assert "无关" in captured.get("message", ""), "应注入婉拒指令"


async def test_on_topic_complex_request_not_misjudged():
    """别误伤：学习相关的复杂请求(off_topic=False)照常走编排器，行为不变。"""
    planner = FakePlanner([_plan(_s("s1"))])
    orch = _mk(planner, FakeCritic(reviews=(True,)), [])
    orch._is_simple = _acoro(False)          # 复杂
    orch._is_off_topic = _acoro(False)       # 相关
    evs = [e async for e in orch.run("系统梳理一下 Java 类加载机制")]
    assert len(planner.seen_tools) == 1, "学习相关的复杂请求仍应正常进 Planner"


async def test_simple_request_skips_off_topic_check():
    """判简单的请求不做无关判定(省调用、且简单直答由 app_system_prompt 兜)：
    _is_off_topic 若被调用就抛错，用它证明没被调。"""
    async def _boom(*a, **k):
        raise AssertionError("简单请求不该触发 off_topic 判定")
    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(), [], triage_simple=True)
    orch._is_off_topic = _boom
    # triage_simple=True → _is_simple 返回 True → 走简单直答，不应碰 off_topic
    evs = [e async for e in orch.run("你好呀")]
    routes = [e for e in evs if isinstance(e, Progress) and e.scope == "route"]
    assert routes and routes[0].detail.get("mode") == "simple"


async def test_is_off_topic_parses_and_defaults_safe():
    """_is_off_topic 解析：off_topic → True，其余 → False；异常一律 False(不误伤)。"""
    orch = _mk(FakePlanner([]), FakeCritic(), [])

    orch._fast_complete = _acoro("off_topic")
    assert await orch._is_off_topic("推荐股票") is True

    orch._fast_complete = _acoro("on_topic")
    assert await orch._is_off_topic("讲讲多态") is False

    # 判不出/脏输出 → 不判无关(宁可漏判不可误判)
    orch._fast_complete = _acoro("不确定")
    assert await orch._is_off_topic("模糊请求") is False

    async def _raise(*a, **k):
        raise RuntimeError("端点抖动")
    orch._fast_complete = _raise
    assert await orch._is_off_topic("任意") is False, "异常必须放过，绝不误伤"


# ── 回归：终局裁判必须看得到最近对话 ────────────────────────────────────────────
# 真实误判：用户与 AI 聊了半天 LlamaIndex，接着说「帮我写个 hello world 看一下」。
# AI 给的 LlamaIndex 版 hello world 是对的，裁判却只看见孤立的那一句，判「用了复杂的
# RAG 框架、严重答非所问、未能达成基本目标」，把正确答复打回重答。
_LLAMA_DIALOGUE = "用户：LlamaIndex 怎么建索引？\nAI：先 SimpleDirectoryReader 读文档，再 VectorStoreIndex…"


async def _drafting_simple(msg, budget=None, *, context=None, registry=None,
                           prefer_main=False, skill_hint=""):
    """出一份草稿的简单直答：空产出会被直接收尾、根本不进校验，测不到裁判。"""
    yield TextDelta(text="from llama_index.core import VectorStoreIndex ...")


async def test_simple_verified_feeds_recent_dialogue_to_critic():
    """简单直答（用户实际踩到的那条路）：上文必须喂给终局裁判。"""
    critic = FakeCritic()
    orch = _mk(FakePlanner([_plan(_s("s1"))]), critic, [], triage_simple=True)
    orch._simple_answer = _drafting_simple
    await _run_kw(orch, "帮我写个 hello world 看一下", recent_dialogue=_LLAMA_DIALOGUE)
    assert critic.last_dialogue == _LLAMA_DIALOGUE, "简单直答的终局校验没带上文 → 承接前文的请求会被判答非所问"


async def test_multi_step_review_feeds_recent_dialogue_to_critic():
    """多步路径的终局 review 同样要带上文。"""
    critic = FakeCritic()
    orch = _mk(FakePlanner([_plan(_s("s1"), _s("s2"))]), critic, [])
    await _run_kw(orch, "把刚才那套流程整理成文档", recent_dialogue=_LLAMA_DIALOGUE)
    assert critic.last_dialogue == _LLAMA_DIALOGUE


async def test_review_prompt_carries_dialogue_and_anti_misjudge_rule():
    """裁判的 prompt 里既要有上文正文，也要有「别因脱离上文显得跑题就判不过」这条。"""
    from app.orchestration.critic import REVIEW_SYSTEM, _review_user
    from app.orchestration.plan import Artifact

    plan, arts = om._one_step_plan("帮我写个 hello world 看一下", "from llama_index...")
    user = _review_user("帮我写个 hello world 看一下", plan, arts, _LLAMA_DIALOGUE)
    assert "LlamaIndex" in user and "最近几轮对话" in user
    assert "答非所问" in REVIEW_SYSTEM and "延续上文" in REVIEW_SYSTEM


def test_review_does_not_re_truncate_the_dialogue():
    """窗口只归 chat._recent_dialogue 一处管：裁判这里原样用，不再截第二刀。

    截第二刀有两重坏处：同一个策略拆到两处，且终局裁判会比交付门 judge 拿得更少——
    同一轮里两个裁判看见的上文不一样，误判起来无从对账。
    """
    from app.orchestration.critic import _review_user

    plan, arts = om._one_step_plan("g", "a")
    dialogue = "用户：先聊 LlamaIndex\n" + "AI：中间还说了很多\n" * 200
    user = _review_user("g", plan, arts, dialogue)
    assert dialogue in user, "上文被就地截断了"

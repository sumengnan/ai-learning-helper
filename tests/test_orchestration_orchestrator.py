from harness.events import Progress, RunError, RunFinished, RunStarted, TextDelta
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
    def __init__(self, validate_ok=True, reviews=(True,)):
        self._validate_ok = validate_ok
        self._reviews = list(reviews); self._ri = 0
    async def validate(self, step, artifact):
        return Verdict(ok=self._validate_ok, reason="")
    async def review(self, goal, plan, artifacts):
        r = self._reviews[min(self._ri, len(self._reviews) - 1)]; self._ri += 1
        return Review(accept=r, feedback="补一下X")


class FakeExecutor:
    """每步产出 summary=step.id 的 Artifact；记录执行顺序供并行断言。"""
    def __init__(self, order):
        self._order = order
    async def execute(self, step, deps, hint="", *, registry=None):
        from app.orchestration.executor import StepArtifact
        self._order.append(step.id)
        yield Progress(f"subagent:executor:{step.id}", "开始")
        yield StepArtifact(Artifact(summary=f"done-{step.id}"))


def _mk(planner, critic, order, triage_simple=False, synth="最终答复", max_replan=2):
    async def fake_triage(msg):
        return triage_simple
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
    orch._synthesize = fake_synth
    orch._simple_answer = fake_simple
    orch._max_replan = max_replan
    orch._max_step_retry = 2
    orch._budget = None
    orch._budget_factory = None
    return orch


async def _run(orch, msg="做点复杂的事"):
    return [ev async for ev in orch.run(msg)]


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
    orch = _mk(FakePlanner([_plan(_s("s1")), _plan(_s("s2"))]),
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
        async def review(self, goal, plan, artifacts):
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
    orch = _mk(FakePlanner([_plan(_s("s1")), _plan(_s("s2"))]),
               FakeCritic(validate_ok=True, reviews=(False, True)), order)
    orch._synthesize = capture_synth
    events = await _run(orch)
    assert isinstance(events[-1], RunFinished)
    assert "s1" in got and "s2" in got, f"重规划丢了上一轮产物：{sorted(got)}"


async def test_validate_fail_retries_bounded_then_failed():
    order = []
    # validate 恒失败：s1 会重试到 max_step_retry 后置 failed；review 放行 → 带残缺定稿
    orch = _mk(FakePlanner([_plan(_s("s1"))]),
               FakeCritic(validate_ok=False, reviews=(True,)), order)
    events = await _run(orch)
    assert isinstance(events[-1], RunFinished)   # 不因单步失败崩溃
    assert order.count("s1") == 2                # 初次 + 1 次重试（max_step_retry=2）


class _CountingCritic:
    def __init__(self): self.reviews = 0
    async def validate(self, step, artifact):
        return Verdict(ok=True, reason="")
    async def review(self, goal, plan, artifacts):
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
    orch = _mk(FakePlanner([_plan(_s("s1"))]), critic, [])
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
            return _plan(_s("s1"))
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
        async def execute(self, step, deps, hint="", *, registry=None):
            record_usage(Usage(0, 0, 100), 0.01)
            yield StepArtifact(Artifact(summary=f"done-{step.id}"))

    class UCritic:
        async def validate(self, step, art):
            record_usage(Usage(0, 0, 10), 0.001); return Verdict(ok=True, reason="")
        async def review(self, goal, plan, arts):
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
    orch = _mk(FakePlanner([_plan(_s("s1"))]),
               FakeCritic(validate_ok=True, reviews=(True,)), order)
    events = await _run(orch)
    verify = [e for e in events if isinstance(e, Progress) and e.scope == "verify"]
    assert any(e.status == "running" for e in verify), "应先发一条校验中(running)"
    assert any(e.status == "ok" and "通过" in e.text for e in verify), "通过时应发 ok 终态"


async def test_review_fail_emits_verify_error_then_retries():
    """校验不通过：先发 scope=verify(error) 带缺口说明，再重规划、最终通过再发一条 ok，形成校验历史。"""
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1")), _plan(_s("s1"))]),
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
        async def execute(self, step, deps, hint="", *, registry=None):
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
        async def execute(self, step, deps, hint="", *, registry=None):
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
        async def execute(self, step, deps, hint="", *, registry=None):
            from app.orchestration.executor import StepArtifact
            seen["reg"] = registry
            yield StepArtifact(Artifact(summary="x"))
    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(reviews=(True,)), [])
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
            return _plan(_s("s1"))
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
    async def counting_triage(msg):
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
    """非寒暄消息仍交 LLM triage 判简单/复杂。"""
    calls = {"triage": 0}
    async def counting_triage(msg):
        calls["triage"] += 1
        return True
    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(), [])
    orch._is_simple = counting_triage
    _ = [ev async for ev in orch.run("帮我分析这段代码的时间复杂度")]
    assert calls["triage"] == 1


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

    async def counting_triage(msg):
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
        async def execute(self, step, deps, hint="", *, registry=None):
            record_usage(Usage(0, 0, 100), 0.01, "fast-model")
            yield StepArtifact(Artifact(summary=f"done-{step.id}"))

    class MCritic:
        async def validate(self, step, art):
            record_usage(Usage(0, 0, 10), 0.001, "fast-model"); return Verdict(ok=True, reason="")
        async def review(self, goal, plan, arts):
            record_usage(Usage(0, 0, 20), 0.002, "main-model"); return Review(accept=True, feedback="")

    class MPlanner:
        async def plan(self, goal, recent_dialogue="", skill_hint="", *, tools_desc=""):
            record_usage(Usage(0, 0, 30), 0.003, "main-model"); return _plan(_s("s1"))
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
    assert set(agg) == {"fast-model", "main-model"}
    assert agg["fast-model"]["tok"] == 110      # exec 100 + validate 10
    assert agg["main-model"]["tok"] == 100      # plan 30 + review 20 + synth 50
    assert abs(agg["fast-model"]["cost"] - 0.011) < 1e-9
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
    planner = FakePlanner([_plan(_s("s1")), _plan(_s("s2"))])
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
    planner = FakePlanner([_plan(_s("s1")), _plan(_s("s2"))])
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

    async def execute(self, step, deps, hint="", *, registry=None):
        from app.orchestration.executor import StepArtifact
        self._order.append(step.id)
        yield Progress(f"subagent:executor:{step.id}", "开始")
        yield StepArtifact(Artifact(summary="命令未执行：用户拒绝了该操作"), terminal=True)


async def test_user_denial_is_terminal_no_retry():
    """回归：拒绝后步骤校验失败 → 编排器按普通失败重试，把同一个弹窗又怼给用户几次。
    重试只对偶发故障有意义；人已经说了不，重跑不会有不同答案。"""
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"))]),
               FakeCritic(validate_ok=False, reviews=(True,)), order)
    orch._executor = _DenyingExecutor(order)
    events = await _run(orch)

    assert order == ["s1"], f"被拒的步骤不该重跑，实际执行了 {len(order)} 次"
    assert isinstance(events[-1], RunFinished)      # 仍正常收尾，不硬崩


async def test_ordinary_failure_still_retries():
    """反向：普通失败照旧重试——别把这条护栏做成全局禁用重试。"""
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"))]),
               FakeCritic(validate_ok=False, reviews=(True,)), order)
    await _run(orch)
    assert order.count("s1") == 2      # 初次 + 1 次重试（max_step_retry=2）

def test_synth_user_does_not_glue_step_id_to_content():
    """汇总提示词同样不能把步骤 id 粘在正文前，否则最终答复里会漏出 [s1] 残留。"""
    from app.orchestration.orchestrator import _synth_user
    from app.orchestration.plan import Artifact
    out = _synth_user("目标", {"s1": Artifact(summary="# 标题\n正文")})
    assert "[s1] # 标题" not in out
    assert "# 标题" in out and "s1" in out

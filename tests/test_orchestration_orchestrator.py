from harness.events import Progress, RunError, RunFinished, RunStarted, TextDelta
from app.orchestration.orchestrator import Orchestrator
from app.orchestration.plan import Artifact, Plan, PlanStep, Verdict, Review
from app.orchestration.planner import PlannerError


# ---- 测试替身 ----
class FakePlanner:
    def __init__(self, plans, raise_on_plan=False):
        self._plans = list(plans); self._i = 0
        self._raise_on_plan = raise_on_plan
    async def plan(self, goal):
        if self._raise_on_plan:
            raise PlannerError("boom")
        p = self._plans[0]; return p
    async def replan(self, goal, plan, feedback):
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
    async def execute(self, step, deps, hint=""):
        from app.orchestration.executor import StepArtifact
        self._order.append(step.id)
        yield Progress(f"subagent:executor:{step.id}", "开始")
        yield StepArtifact(Artifact(summary=f"done-{step.id}"))


def _mk(planner, critic, order, triage_simple=False, synth="最终答复", max_replan=2):
    async def fake_triage(msg):
        return triage_simple
    async def fake_synth(goal, artifacts):
        from harness.events import TextDelta
        yield TextDelta(text=synth)
    async def fake_simple(msg, budget=None):
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
    async def capture_synth(goal, artifacts):
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
    async def capture_synth(goal, artifacts):
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


async def test_run_aggregates_all_usage_incl_planner_critic():
    """所有子调用的 token 用量（planner + executor + critic validate/review + synthesize）
    汇总成一条 ModelUsage，前端才显示得出总量。"""
    from harness.events import ModelUsage
    from harness.usage import Usage
    from app.orchestration.usage_ctx import record_usage
    from app.orchestration.executor import StepArtifact
    from app.orchestration.plan import Artifact

    class UExec:
        async def execute(self, step, deps, hint=""):
            record_usage(Usage(0, 0, 100), 0.01)
            yield StepArtifact(Artifact(summary=f"done-{step.id}"))

    class UCritic:
        async def validate(self, step, art):
            record_usage(Usage(0, 0, 10), 0.001); return Verdict(ok=True, reason="")
        async def review(self, goal, plan, arts):
            record_usage(Usage(0, 0, 20), 0.002); return Review(accept=True, feedback="")

    class UPlanner:
        async def plan(self, goal):
            record_usage(Usage(0, 0, 30), 0.003); return _plan(_s("s1"), _s("s2"))
        async def replan(self, g, p, f):
            return _plan(_s("s1"))

    async def usynth(goal, arts):
        record_usage(Usage(0, 0, 50), 0.005); yield TextDelta(text="答复")

    orch = _mk(UPlanner(), UCritic(), [])
    orch._executor = UExec()
    orch._synthesize = usynth
    events = [ev async for ev in orch.run("复杂")]
    usages = [e for e in events if isinstance(e, ModelUsage)]
    assert len(usages) == 1, "应只发一条聚合"
    # plan 30 + s1/s2 各 100 + validate 各 10 + review 20 + synth 50 = 320
    assert usages[0].usage.total_tokens == 30 + 100 * 2 + 10 * 2 + 20 + 50
    assert abs((usages[0].cost_usd or 0) - (0.003 + 0.01 * 2 + 0.001 * 2 + 0.002 + 0.005)) < 1e-9


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
    async def capture_synth(goal, artifacts):
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
        async def execute(self, step, deps, hint=""):
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
        async def execute(self, step, deps, hint=""):
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

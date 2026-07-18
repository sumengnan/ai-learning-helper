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
    async def fake_simple(msg):
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


async def test_deadlock_emits_run_error():
    order = []
    # s1 依赖 s2、s2 依赖 s1 是非法 DAG，但 FakePlanner 不校验；
    # 用「依赖一个永不就绪(不存在于就绪逻辑)的步」构造死锁：s1 依赖 sX（sX 不在计划里）
    orch = _mk(FakePlanner([_plan(_s("s1", deps=["sX"]))]), FakeCritic(), order)
    events = await _run(orch)
    assert any(isinstance(e, RunError) for e in events)
    assert not isinstance(events[-1], RunFinished)


async def test_validate_fail_retries_bounded_then_failed():
    order = []
    # validate 恒失败：s1 会重试到 max_step_retry 后置 failed；review 放行 → 带残缺定稿
    orch = _mk(FakePlanner([_plan(_s("s1"))]),
               FakeCritic(validate_ok=False, reviews=(True,)), order)
    events = await _run(orch)
    assert isinstance(events[-1], RunFinished)   # 不因单步失败崩溃
    assert order.count("s1") == 2                # 初次 + 1 次重试（max_step_retry=2）


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


async def test_budget_exceeded_aborts():
    from harness.reliability.budget import BudgetExceeded
    class BadBudget:
        def start(self): pass
        def check(self): raise BudgetExceeded("超预算")
    order = []
    orch = _mk(FakePlanner([_plan(_s("s1"))]), FakeCritic(reviews=(True,)), order)
    orch._budget = BadBudget()
    events = await _run(orch)
    from harness.events import RunError, RunFinished
    assert any(isinstance(e, RunError) for e in events)
    assert not isinstance(events[-1], RunFinished)


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
    first = await agen.__anext__()          # 拿到 s_fast 的首个 Progress，此时两 worker 都在途
    assert isinstance(first, Progress)
    await agen.aclose()                      # 模拟提前放弃 → 应取消未完成 worker
    await asyncio.sleep(0)                    # 放行取消回调
    assert "s_slow" in cancelled             # 悬挂 worker 被取消（未取消时此断言失败）

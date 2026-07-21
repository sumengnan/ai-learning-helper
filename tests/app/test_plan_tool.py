import json
import sqlite3

import pytest
from pydantic import ValidationError

from harness import progress
from harness.events import Progress
from app.tools.plan_tool import UpdatePlanTool, PlanStep, PLAN_SYSTEM_GUIDANCE


@pytest.fixture(autouse=True)
def _sqlite_allow_cross_thread(monkeypatch):
    # 同 tests/app/test_api.py：TestClient 用 anyio 的 blocking portal 在独立线程里
    # 跑 ASGI app，与本测试构造 CheckpointStore/TrajectoryStore 的主线程不同；harness
    # 这两个 store 用 sqlite3.connect() 默认 check_same_thread=True，跨线程访问会抛
    # ProgrammingError。这里仅为测试放宽该限制（不改 harness 代码）。
    original_connect = sqlite3.connect

    def _patched_connect(*args, **kwargs):
        kwargs.setdefault("check_same_thread", False)
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _patched_connect)


async def test_update_plan_emits_ordered_snapshot():
    tool = UpdatePlanTool()
    got = []
    token = progress.set_emitter(got.append)
    try:
        r = await tool.run(tool.Params(steps=[
            PlanStep(title="查资料", status="running"),
            PlanStep(title="汇总", status="pending"),
        ]))
    finally:
        progress.reset_emitter(token)

    evs = [e for e in got if isinstance(e, Progress)]
    assert len(evs) == 1
    assert evs[0].scope == "plan" and evs[0].key == "plan"
    assert json.loads(evs[0].text) == [
        {"title": "查资料", "status": "running"},
        {"title": "汇总", "status": "pending"},
    ]
    assert "2 步" in r


async def test_update_plan_reports_failed_count():
    tool = UpdatePlanTool()
    got = []
    token = progress.set_emitter(got.append)
    try:
        r = await tool.run(tool.Params(steps=[
            PlanStep(title="算", status="failed"),
            PlanStep(title="重试：换沙箱", status="running"),
        ]))
    finally:
        progress.reset_emitter(token)
    assert "1 失败" in r


async def test_update_plan_empty_raises_and_emits_nothing():
    from harness.tools.base import ToolError
    tool = UpdatePlanTool()
    got = []
    token = progress.set_emitter(got.append)
    try:
        with pytest.raises(ToolError):
            await tool.run(tool.Params(steps=[]))
    finally:
        progress.reset_emitter(token)
    assert got == []


def test_update_plan_rejects_unknown_status():
    with pytest.raises(ValidationError):
        UpdatePlanTool.Params(steps=[{"title": "x", "status": "bogus"}])


def test_guidance_mentions_tool_and_gating():
    assert "update_plan" in PLAN_SYSTEM_GUIDANCE
    assert "简单问答" in PLAN_SYSTEM_GUIDANCE


def _plan_client(make_mock, turns):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import AppConfig
    from app.assembly import Harness
    from app.conversations import ConversationStore
    from app.documents import DocumentStore
    from harness.tools.base import ToolRegistry
    from harness.persistence.checkpoint import CheckpointStore
    from harness.persistence.trajectory import TrajectoryStore, TrajectorySink

    reg = ToolRegistry(); reg.register(UpdatePlanTool())
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=make_mock(turns), registry=reg,
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj),
                      system_prompt="你是助手")
    store = ConversationStore(":memory:")
    app = create_app(config=AppConfig(api_key="k", app_db_path=":memory:"),
                     harness=harness, store=store, doc_store=DocumentStore(":memory:"))
    return TestClient(app)


def _sse(resp):
    return [json.loads(l[6:]) for l in resp.iter_lines() if l and l.startswith("data: ")]


def test_chat_emits_and_persists_plan(make_mock, tool_turn, text_turn):
    args = '{"steps":[{"title":"查资料","status":"running"},{"title":"汇总","status":"pending"}]}'
    client = _plan_client(make_mock, [tool_turn("update_plan", args, call_id="p1"),
                                      text_turn("完成")])
    r = client.post("/api/auth/register", json={"username": "u", "full_name": "测试用户", "password": "pw1234"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]

    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": "帮我查并汇总"}, headers=h) as resp:
        events = _sse(resp)

    plan = [e for e in events if e["type"] == "Progress" and e["data"]["scope"] == "plan"]
    assert plan, "SSE 流应含 scope=plan 的 Progress"
    steps = json.loads(plan[-1]["data"]["text"])
    assert [s["title"] for s in steps] == ["查资料", "汇总"]

    msgs = client.get(f"/api/conversations/{cid}/messages", headers=h).json()
    assistant = next(m for m in msgs if m["role"] == "assistant")
    assert any(p["scope"] == "plan" for p in (assistant.get("progress") or []))


# ---- 每步耗时 ----

class _FakeTime:
    """替掉 plan_tool 命名空间里的 time 模块，让计时可断言（不动全局 time）。

    monotonic 与 time 同步推进，但基准不同——正如真实的两者：前者量耗时，
    后者给前端读秒的 epoch 起点。
    """
    _EPOCH0 = 1_700_000_000.0

    def __init__(self, t0: float = 100.0) -> None:
        self.now = t0

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self._EPOCH0 + self.now


async def _emit_plan(tool, steps) -> list[dict]:
    got = []
    token = progress.set_emitter(got.append)
    try:
        await tool.run(tool.Params(steps=steps))
    finally:
        progress.reset_emitter(token)
    return json.loads([e for e in got if isinstance(e, Progress)][-1].text)


@pytest.fixture
def plan_clock(monkeypatch):
    """装好假时钟 + 本轮计时表，产出 (推进时间的钩子, 工具)。"""
    from app.tools import plan_tool
    clk = _FakeTime()
    monkeypatch.setattr(plan_tool, "time", clk)
    token = plan_tool.set_plan_clock()
    try:
        yield clk, UpdatePlanTool()
    finally:
        plan_tool.reset_plan_clock(token)


async def test_step_elapsed_measured_from_running_to_done(plan_clock):
    clk, tool = plan_clock
    s1 = await _emit_plan(tool, [PlanStep(title="查资料", status="running")])
    assert "elapsed_ms" not in s1[0]              # 还在跑：不给耗时，别编

    clk.now += 3.5
    s2 = await _emit_plan(tool, [PlanStep(title="查资料", status="done")])
    assert s2[0]["elapsed_ms"] == 3500

    clk.now += 900                                 # 后续快照里该步耗时须定格
    s3 = await _emit_plan(tool, [PlanStep(title="查资料", status="done")])
    assert s3[0]["elapsed_ms"] == 3500


async def test_failed_step_also_timed(plan_clock):
    clk, tool = plan_clock
    await _emit_plan(tool, [PlanStep(title="算", status="running")])
    clk.now += 2
    s = await _emit_plan(tool, [PlanStep(title="算", status="failed")])
    assert s[0]["elapsed_ms"] == 2000


async def test_step_never_running_gets_no_elapsed(plan_clock):
    """模型跳过 running 直接置 done → 没有起点，不编耗时。"""
    clk, tool = plan_clock
    clk.now += 5
    s = await _emit_plan(tool, [PlanStep(title="汇总", status="done")])
    assert "elapsed_ms" not in s[0]


async def test_timing_follows_title_not_index(plan_clock):
    """失败重试会在中间插入新步骤、下标整体错位 → 计时必须跟着 title 走。"""
    clk, tool = plan_clock
    await _emit_plan(tool, [PlanStep(title="A", status="running"),
                            PlanStep(title="B", status="pending")])
    clk.now += 2
    # A 失败，其后插入重试步骤 → B 的下标从 1 变成 2
    await _emit_plan(tool, [PlanStep(title="A", status="failed"),
                            PlanStep(title="重试A", status="running"),
                            PlanStep(title="B", status="pending")])
    clk.now += 5
    s = await _emit_plan(tool, [PlanStep(title="A", status="failed"),
                                PlanStep(title="重试A", status="done"),
                                PlanStep(title="B", status="running")])
    by = {x["title"]: x for x in s}
    assert by["A"]["elapsed_ms"] == 2000          # 定格在失败那刻，没被插入搅乱
    assert by["重试A"]["elapsed_ms"] == 5000
    assert "elapsed_ms" not in by["B"]            # 刚开跑


async def test_no_clock_set_emits_untimed_snapshot():
    """未设置计时表（纯 harness 用法）→ 不带 elapsed_ms，行为同旧版。"""
    s = await _emit_plan(UpdatePlanTool(), [PlanStep(title="x", status="done")])
    assert s == [{"title": "x", "status": "done"}]


def test_chat_persists_step_elapsed_for_refresh(make_mock, tool_turn, text_turn):
    """耗时必须随 plan 落进 progress 列：刷新重新拉 messages 时仍在，不会变没。"""
    a1 = '{"steps":[{"title":"查资料","status":"running"},{"title":"汇总","status":"pending"}]}'
    a2 = '{"steps":[{"title":"查资料","status":"done"},{"title":"汇总","status":"done"}]}'
    client = _plan_client(make_mock, [tool_turn("update_plan", a1, call_id="p1"),
                                      tool_turn("update_plan", a2, call_id="p2"),
                                      text_turn("完成")])
    r = client.post("/api/auth/register", json={"username": "u", "full_name": "测试用户", "password": "pw1234"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": "帮我查并汇总"}, headers=h) as resp:
        _sse(resp)

    # 模拟刷新：重新拉取该对话的消息，plan 快照里的耗时应当还在
    msgs = client.get(f"/api/conversations/{cid}/messages", headers=h).json()
    assistant = next(m for m in msgs if m["role"] == "assistant")
    plans = [p for p in (assistant.get("progress") or []) if p["scope"] == "plan"]
    steps = json.loads(plans[-1]["text"])
    by = {s["title"]: s for s in steps}
    assert by["查资料"].get("elapsed_ms") is not None   # 走过 running → 有耗时且已落库
    assert "elapsed_ms" not in by["汇总"]               # 没走过 running → 不编


async def test_running_step_carries_epoch_start_for_live_ticking(plan_clock):
    """进行中的步骤要带 started_at_ms（epoch 毫秒）：前端拿它和 Date.now() 相减读秒。
    monotonic 只在本进程内有意义，给不了浏览器。"""
    clk, tool = plan_clock
    s = await _emit_plan(tool, [PlanStep(title="查资料", status="running")])
    assert s[0]["started_at_ms"] == int(clk.time() * 1000)
    assert "elapsed_ms" not in s[0]          # 还没结束，不给定格值


async def test_running_step_start_is_stable_across_snapshots(plan_clock):
    """起点不能每次快照都刷新，否则读秒会被一路清零。"""
    clk, tool = plan_clock
    s1 = await _emit_plan(tool, [PlanStep(title="查", status="running")])
    clk.now += 30
    s2 = await _emit_plan(tool, [PlanStep(title="查", status="running")])
    assert s2[0]["started_at_ms"] == s1[0]["started_at_ms"]


async def test_finished_step_drops_start_and_keeps_elapsed(plan_clock):
    """结束后只留定格耗时：起点已无用，留着反而可能被误读成还在跑。"""
    clk, tool = plan_clock
    await _emit_plan(tool, [PlanStep(title="查", status="running")])
    clk.now += 4
    s = await _emit_plan(tool, [PlanStep(title="查", status="done")])
    assert s[0]["elapsed_ms"] == 4000
    assert "started_at_ms" not in s[0]


def test_chat_persists_running_step_start_for_refresh(make_mock, tool_turn, text_turn):
    """刷新时若某步仍在跑，落库的快照里要有 started_at_ms，前端才能接着读秒。"""
    a1 = '{"steps":[{"title":"查资料","status":"running"},{"title":"汇总","status":"pending"}]}'
    client = _plan_client(make_mock, [tool_turn("update_plan", a1, call_id="p1"),
                                      text_turn("完成")])
    r = client.post("/api/auth/register", json={"username": "u", "full_name": "测试用户", "password": "pw1234"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": "查一下"}, headers=h) as resp:
        _sse(resp)

    msgs = client.get(f"/api/conversations/{cid}/messages", headers=h).json()
    assistant = next(m for m in msgs if m["role"] == "assistant")
    plans = [p for p in (assistant.get("progress") or []) if p["scope"] == "plan"]
    by = {s["title"]: s for s in json.loads(plans[-1]["text"])}
    assert by["查资料"]["started_at_ms"] > 0          # 仍在跑 → 起点已落库
    assert "started_at_ms" not in by["汇总"]          # 没开始 → 无起点

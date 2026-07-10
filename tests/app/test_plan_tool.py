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
    r = client.post("/api/auth/register", json={"username": "u", "password": "pw1234"})
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

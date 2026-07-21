"""清单收尾的端到端接线：模型留下半截清单 → 交付前被补齐 → 且刷新后仍是补齐的那份。

复刻真实轨迹的形状：模型调 update_plan 留下 [running, pending]，然后直接给最终答案
（正是它 1/6 的多步任务会干的事）。
"""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.assembly import Harness
from app.config import AppConfig
from app.conversations import ConversationStore
from app.documents import DocumentStore
from app.main import create_app
from harness.persistence.checkpoint import CheckpointStore
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink
from harness.tools.base import ToolRegistry
from app.tools.plan_tool import UpdatePlanTool


@pytest.fixture(autouse=True)
def _sqlite_allow_cross_thread(monkeypatch):
    orig = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect",
                        lambda *a, **k: orig(*a, **{**k, "check_same_thread": False}))


STALE_PLAN = [{"title": "查资料", "status": "running"},
              {"title": "写笔记", "status": "pending"}]


def _client(make_mock, tool_turn, text_turn, monkeypatch, finalizer):
    # 收尾员是 build_judge_completer 的产物。它在 make_chat_router 内部才 import，
    # 故必须打在源模块 app.completion 上（chat 模块上没有这个属性）
    monkeypatch.setattr("app.completion.build_judge_completer", lambda *a, **k: finalizer)
    traj = TrajectoryStore(":memory:")
    turns = [tool_turn("update_plan", json.dumps({"steps": STALE_PLAN})),
             text_turn("查完了，笔记也写好了。")]
    # update_plan 由 build_harness 注册（assembly.py），直接构造 Harness 时须自己补上
    reg = ToolRegistry(); reg.register(UpdatePlanTool())
    harness = Harness(client=make_mock(turns), registry=reg,
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="你是助手")
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None,
                    enable_answer_gate=False)
    store = ConversationStore(":memory:")
    app = create_app(config=cfg, harness=harness, store=store,
                     doc_store=DocumentStore(":memory:"))
    return TestClient(app), store


def _auth(c):
    r = c.post("/api/auth/register", json={"username": "u", "full_name": "测试用户", "password": "pw1234"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _chat(c, h):
    cid = c.post("/api/conversations", json={}, headers=h).json()["id"]
    with c.stream("POST", "/api/chat",
                  json={"conversation_id": cid, "message": "查点资料写成笔记"},
                  headers=h) as r:
        events = [json.loads(ln[6:]) for ln in r.iter_lines()
                  if ln.startswith("data: ") and ln[6:] != "[DONE]"]
    return cid, events


def _plans(events):
    return [json.loads(e["data"]["text"]) for e in events
            if e["type"] == "Progress" and e["data"].get("scope") == "plan"]


def _stored_plan(store, cid):
    """刷新后前端看到的那份 —— 只认 progress 列。"""
    rows = store.ui_messages(cid)
    prog = [p for m in rows for p in (m.get("progress") or []) if p.get("scope") == "plan"]
    return json.loads(prog[-1]["text"]) if prog else None


async def _ok_finalizer(system, user):
    return json.dumps([{"title": "查资料", "status": "done"},
                       {"title": "写笔记", "status": "done"}])


def test_stale_plan_is_finalized_before_delivery(make_mock, tool_turn, text_turn, monkeypatch):
    c, store = _client(make_mock, tool_turn, text_turn, monkeypatch, _ok_finalizer)
    cid, events = _chat(c, _auth(c))
    plans = _plans(events)
    assert len(plans) == 2, "应有两份：模型那份半截的 + 交付前补齐的"
    assert [s["status"] for s in plans[0]] == ["running", "pending"]   # 模型留下的
    assert [s["status"] for s in plans[-1]] == ["done", "done"]        # 补齐后的
    # 刷新后必须也是补齐的那份：progress 列是刷新后的唯一依据，不写进去等于白补
    assert [s["status"] for s in _stored_plan(store, cid)] == ["done", "done"]


def test_clean_plan_costs_no_extra_call(make_mock, tool_turn, text_turn, monkeypatch):
    """清单已收尾（84% 的情况）→ 一次收尾调用都不该发生。"""
    calls = []

    async def _spy(system, user):
        calls.append(user)
        return "[]"

    clean = [{"title": "查资料", "status": "done"}]
    monkeypatch.setattr("app.completion.build_judge_completer", lambda *a, **k: _spy)
    traj = TrajectoryStore(":memory:")
    reg = ToolRegistry(); reg.register(UpdatePlanTool())
    harness = Harness(client=make_mock([tool_turn("update_plan", json.dumps({"steps": clean})),
                                        text_turn("好了。")]),
                      registry=reg, checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="你是助手")
    app = create_app(config=AppConfig(api_key="k", app_db_path=":memory:", _env_file=None,
                                      enable_answer_gate=False),
                     harness=harness, store=ConversationStore(":memory:"),
                     doc_store=DocumentStore(":memory:"))
    c = TestClient(app)
    _chat(c, _auth(c))
    assert calls == [], "清单已收尾却还是花了一次调用"


def test_finalizer_failure_degrades_to_original_plan(make_mock, tool_turn, text_turn, monkeypatch):
    """收尾员挂了 → 保留半截清单照常交付（前端会如实标『状态未知』），绝不打断本轮。"""
    async def _boom(system, user):
        raise RuntimeError("收尾员挂了")

    c, store = _client(make_mock, tool_turn, text_turn, monkeypatch, _boom)
    cid, events = _chat(c, _auth(c))
    assert any(e["type"] == "RunFinished" for e in events)            # 答案照常交付
    assert [s["status"] for s in _plans(events)[-1]] == ["running", "pending"]
    assert [s["status"] for s in _stored_plan(store, cid)] == ["running", "pending"]


def test_untrustworthy_finalizer_output_is_rejected(make_mock, tool_turn, text_turn, monkeypatch):
    """模型把两步改成一步 → 驳回，宁可留半截也不显示一份「不是用户那份」的清单。"""
    async def _mangle(system, user):
        return json.dumps([{"title": "我自己编的一步", "status": "done"}])

    c, store = _client(make_mock, tool_turn, text_turn, monkeypatch, _mangle)
    cid, events = _chat(c, _auth(c))
    last = _plans(events)[-1]
    assert [s["title"] for s in last] == ["查资料", "写笔记"]
    assert [s["status"] for s in last] == ["running", "pending"]
    assert _stored_plan(store, cid)[0]["title"] == "查资料"

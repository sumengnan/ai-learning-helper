"""编排器接入 /api/chat 主流程：开关开且 harness.orchestrator 存在时，主流程改为消费
Orchestrator.run() 的事件流并走现有 SSE 序列化；开关关闭时维持既有 ReAct 路径。

搭建方式照抄 tests/app/test_plan_finalize_e2e.py（直接构造 Harness + create_app + TestClient
+ c.stream 收 SSE）。
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


@pytest.fixture(autouse=True)
def _sqlite_allow_cross_thread(monkeypatch):
    orig = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect",
                        lambda *a, **k: orig(*a, **{**k, "check_same_thread": False}))


class FakeOrchestrator:
    """签名与 AgentLoop.run(message) 相同，只 yield 既有 Event 类型。"""
    async def run(self, message):
        from harness.events import RunStarted, TextDelta, RunFinished
        from harness.types import Message, Role
        yield RunStarted(run_id="r1")
        yield TextDelta(text="编排答复")
        yield RunFinished(message=Message(role=Role.ASSISTANT, content="编排答复"))


def _client(make_mock, monkeypatch, *, enable_orchestrator, orchestrator):
    traj = TrajectoryStore(":memory:")
    # 模型这轮不会被消费（编排器接管主流程），仍给一份 turn 以防回退到 ReAct 时无输出
    harness = Harness(client=make_mock([]), registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj),
                      system_prompt="你是助手", orchestrator=orchestrator)
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None,
                    enable_answer_gate=False, enable_orchestrator=enable_orchestrator)
    store = ConversationStore(":memory:")
    app = create_app(config=cfg, harness=harness, store=store,
                     doc_store=DocumentStore(":memory:"))
    return TestClient(app), store


def _auth(c):
    r = c.post("/api/auth/register", json={"username": "u", "password": "pw1234"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _chat(c, h):
    cid = c.post("/api/conversations", json={}, headers=h).json()["id"]
    with c.stream("POST", "/api/chat",
                  json={"conversation_id": cid, "message": "帮我查点资料"},
                  headers=h) as r:
        events = [json.loads(ln[6:]) for ln in r.iter_lines()
                  if ln.startswith("data: ") and ln[6:] != "[DONE]"]
    return cid, events


def _last_assistant(store, cid):
    return [m for m in store.ui_messages(cid) if m["role"] == "assistant"][-1]


def test_orchestrator_stream_becomes_main_flow(make_mock, monkeypatch):
    """开关开 + orchestrator 存在 → SSE 里流过编排器的 TextDelta，且该轮正常收尾。"""
    c, store = _client(make_mock, monkeypatch,
                       enable_orchestrator=True, orchestrator=FakeOrchestrator())
    cid, events = _chat(c, _auth(c))

    deltas = [e for e in events if e["type"] == "TextDelta"]
    assert any("编排答复" in e["data"]["text"] for e in deltas), \
        "编排器的 TextDelta 应流过 SSE"
    assert any(e["type"] == "RunFinished" for e in events), "该轮应正常收尾"

    asst = _last_assistant(store, cid)
    assert asst["status"] == "done"
    assert asst["content"] == "编排答复", "落库的本轮 assistant 内容应为编排器的最终答复"

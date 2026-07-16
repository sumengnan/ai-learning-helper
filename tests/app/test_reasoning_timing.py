"""思考耗时（reasoning_ms）：从首个 reasoning token 到首个正文 token 的墙钟，落库供刷新还原。

思考块顶部要显示「思考 N 秒」，实时读秒由前端算，但定格值必须后端量并持久化，
否则刷新后耗时消失（与 elapsed_ms 同规矩）。
"""
import asyncio
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.assembly import Harness
from app.config import AppConfig
from app.conversations import ConversationStore
from app.documents import DocumentStore
from app.main import create_app
from harness.llm.base import StreamChunk, Usage
from harness.persistence.checkpoint import CheckpointStore
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink
from harness.tools.base import ToolRegistry


@pytest.fixture(autouse=True)
def _sqlite_allow_cross_thread(monkeypatch):
    orig = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect",
                        lambda *a, **k: orig(*a, **{**k, "check_same_thread": False}))


class _ThinkingClient:
    """先流式思考、间隔一小段、再流式正文——让思考窗口有可测的墙钟。"""
    def __init__(self, think_gap=0.03, reasoning=True):
        self._gap = think_gap
        self._reasoning = reasoning

    async def stream(self, messages, tools):
        if self._reasoning:
            yield StreamChunk(type="reasoning", text="先想想")
            yield StreamChunk(type="reasoning", text="再想想")
            await asyncio.sleep(self._gap)          # 思考持续这么久
        yield StreamChunk(type="text", text="答案")
        yield StreamChunk(type="done", usage=Usage(10, 5, 15))


def _client(client_obj):
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=client_obj, registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="你是助手")
    store = ConversationStore(":memory:")
    app = create_app(config=AppConfig(api_key="k", app_db_path=":memory:", _env_file=None,
                                      enable_answer_gate=False),
                     harness=harness, store=store, doc_store=DocumentStore(":memory:"))
    return TestClient(app), store


def _auth(c):
    r = c.post("/api/auth/register", json={"username": "u", "password": "pw1234"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _chat(c, h):
    cid = c.post("/api/conversations", json={}, headers=h).json()["id"]
    with c.stream("POST", "/api/chat",
                  json={"conversation_id": cid, "message": "想一下"}, headers=h) as r:
        list(r.iter_lines())
    return cid


def _last_assistant(store, cid):
    return [m for m in store.ui_messages(cid) if m["role"] == "assistant"][-1]


def test_reasoning_ms_measured_and_persisted():
    c, store = _client(_ThinkingClient(think_gap=0.03))
    cid = _chat(c, _auth(c))
    m = _last_assistant(store, cid)
    assert m["reasoning"]                       # 思考文本在
    assert m["reasoning_ms"] is not None
    assert m["reasoning_ms"] >= 25              # 至少覆盖那 30ms 间隔（留余量抗抖动）


def test_no_reasoning_means_no_reasoning_ms():
    """非思考模式（无 reasoning）→ reasoning_ms 为 None，不凭空编一个耗时。"""
    c, store = _client(_ThinkingClient(reasoning=False))
    cid = _chat(c, _auth(c))
    m = _last_assistant(store, cid)
    assert m["reasoning"] is None
    assert m["reasoning_ms"] is None

"""快速模型档的接线：起标题 / 记忆提炼 是否真的用上了它。

单测 build_fast_completer 与 MemoryWriter 本身是不够的——把接线打回主模型，那些测试
照样全绿。这次改动的全部内容就是接线，故必须单独锁住。
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.assembly import Harness
from app.config import AppConfig
from app.conversations import ConversationStore
from app.documents import DocumentStore
from app.main import create_app
from harness.llm.base import StreamChunk
from harness.llm.openai_compat import get_extra_body_override
from harness.persistence.checkpoint import CheckpointStore
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink
from harness.tools.base import ToolRegistry


@pytest.fixture(autouse=True)
def _sqlite_allow_cross_thread(monkeypatch):
    orig = sqlite3.connect

    def _patched(*a, **k):
        k["check_same_thread"] = False
        return orig(*a, **k)
    monkeypatch.setattr(sqlite3, "connect", _patched)


class _ThinkingSpy:
    """截下每次模型调用时实际生效的 extra_body 覆盖（即真会发出去的那份）。"""
    def __init__(self, reply: str = "标题") -> None:
        self.seen: list[dict] = []
        self._reply = reply

    async def stream(self, messages, tools):
        self.seen.append(dict(get_extra_body_override()))
        yield StreamChunk(type="text", text=self._reply)
        yield StreamChunk(type="done")


def _app(spy, **cfg_kw):
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=spy, registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj),
                      system_prompt="你是助手")
    cfg = AppConfig(api_key="k", model="qwen-max", app_db_path=":memory:",
                    _env_file=None, **cfg_kw)
    return create_app(config=cfg, harness=harness, store=ConversationStore(":memory:"),
                      doc_store=DocumentStore(":memory:"))


def _auth(client):
    r = client.post("/api/auth/register", json={"username": "u", "password": "pw1234"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_autotitle_goes_through_fast_completer():
    """起标题必须走快速档：没配 FAST_MODEL 时也要显式关思考。

    此前它用的是不带任何 extra_body 的 build_completer —— 思考开不开由服务端默认决定。
    """
    spy = _ThinkingSpy()
    client = TestClient(_app(spy))
    h = _auth(client)
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    r = client.post(f"/api/conversations/{cid}/autotitle",
                    json={"message": "帮我制定一份 7 天的 AI 学习计划"}, headers=h)
    assert r.status_code == 200
    assert spy.seen, "起标题应发生一次模型调用"
    assert spy.seen[0].get("enable_thinking") is False


def test_autotitle_honours_fast_enable_thinking():
    spy = _ThinkingSpy()
    client = TestClient(_app(spy, fast_enable_thinking=True))
    h = _auth(client)
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    client.post(f"/api/conversations/{cid}/autotitle",
                json={"message": "你好"}, headers=h)
    assert spy.seen[0].get("enable_thinking") is True

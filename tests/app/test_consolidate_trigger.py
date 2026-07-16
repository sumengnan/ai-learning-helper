"""记忆整合的触发接线：攒够 episodic 才整合、后台跑不卡聊天、同会话不并发。"""
import asyncio
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.assembly import Harness
from app.config import AppConfig
from app.conversations import ConversationStore
from app.documents import DocumentStore
from app.main import create_app
from harness.memory.memory import Memory
from harness.memory.record import MemType
from harness.memory.sqlite_backend import SqliteVecBackend
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


class _StubMaintainer:
    """记录 maintain 调用；delay>0 时模拟一次慢整合。"""
    def __init__(self, delay: float = 0.0) -> None:
        self.calls: list[tuple[str, str]] = []
        self._delay = delay

    async def maintain(self, owner_id: str, kind: str) -> dict:
        self.calls.append((owner_id, kind))       # 先记录：断言不必等 delay 结束
        if self._delay:
            await asyncio.sleep(self._delay)
        return {"purged": 0, "clusters": 1, "merged": 3, "created": 1}


class _CountSpy:
    """memory_store 替身：只需 count_by_owner；记录被问过的 mem_type。"""
    def __init__(self, n: int) -> None:
        self.n = n
        self.asked: list = []

    def count_by_owner(self, owner_id: str, kind: str, *, mem_type=None) -> int:
        self.asked.append(mem_type)
        return self.n


def _client(mock_embedder, text_turn, make_mock, *, count: int, maintainer,
            after: int = 20):
    mem = Memory(SqliteVecBackend(":memory:", dimension=64),
                 mock_embedder(dimension=64), chunk_size=1000, overlap=0)
    traj = TrajectoryStore(":memory:")
    harness = Harness(
        client=make_mock([text_turn("回答")]), registry=ToolRegistry(),
        checkpoint_store=CheckpointStore(":memory:"), trajectory_store=traj,
        sink=TrajectorySink(traj), system_prompt="你是助手",
        memory=mem, memory_store=_CountSpy(count), memory_maintainer=maintainer)
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None,
                    context_strategy="layered", context_enable_retrieval=True,
                    memory_consolidate_after=after)
    app = create_app(config=cfg, harness=harness, store=ConversationStore(":memory:"),
                     doc_store=DocumentStore(":memory:"))
    return TestClient(app), harness


def _chat(client) -> float:
    """跑一轮聊天，返回墙钟耗时。"""
    r = client.post("/api/auth/register", json={"username": "u", "password": "pw1234"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    t0 = time.time()
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": "你好"}, headers=h) as resp:
        for _ in resp.iter_lines():
            pass
    return time.time() - t0


def _wait(pred, timeout: float = 3.0) -> bool:
    """等后台任务被调度（fire-and-forget，响应返回时它未必已跑）。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_consolidates_when_episodic_reaches_threshold(mock_embedder, text_turn, make_mock):
    m = _StubMaintainer()
    client, harness = _client(mock_embedder, text_turn, make_mock, count=25, maintainer=m)
    _chat(client)
    assert _wait(lambda: len(m.calls) == 1), "episodic 已达阈值，应在后台整合"
    assert m.calls[0][1] == "conversation"          # kind 固定，owner 为会话 id
    # 必须按 episodic 计数：consolidate 只吃 episodic，用总数会让零 episodic 的会话每轮空转
    assert harness.memory_store.asked == [MemType.EPISODIC]


def test_does_not_consolidate_below_threshold(mock_embedder, text_turn, make_mock):
    m = _StubMaintainer()
    client, _ = _client(mock_embedder, text_turn, make_mock, count=19, maintainer=m)
    _chat(client)
    assert not _wait(lambda: m.calls, timeout=0.4), "没攒够就不该整合"


def test_disabled_when_after_is_zero(mock_embedder, text_turn, make_mock):
    m = _StubMaintainer()
    client, _ = _client(mock_embedder, text_turn, make_mock, count=999, maintainer=m, after=0)
    _chat(client)
    assert not _wait(lambda: m.calls, timeout=0.4), "after=0 应完全关闭"


def test_slow_consolidation_does_not_block_the_chat_turn(mock_embedder, text_turn, make_mock):
    """整合是 fire-and-forget：await 它会让本轮 run 迟迟不结束，
    而 active_run_for_conv 是并发守卫——用户的下一句会直接吃 409。"""
    m = _StubMaintainer(delay=10.0)
    client, _ = _client(mock_embedder, text_turn, make_mock, count=25, maintainer=m)
    elapsed = _chat(client)
    assert elapsed < 3.0, f"整合卡住 10s，聊天却等了 {elapsed:.1f}s —— 说明被 await 了"
    assert _wait(lambda: m.calls), "整合本身仍应被发起"


def test_no_concurrent_consolidation_for_same_conversation(mock_embedder, text_turn, make_mock):
    """同一会话并发整合会互抢 set_superseded；上一轮没跑完就不该再起一个。

    必须用 `with TestClient(app)`：不进上下文时每个请求各起一个 portal，请求一结束就把
    fire-and-forget 的整合任务取消掉，守卫随 finally 一起清空 —— 那是测试假象，不是生产
    行为（uvicorn 的事件循环长驻，任务活得过请求）。带 with 才是对生产的忠实模拟。
    """
    m = _StubMaintainer(delay=10.0)
    mem = Memory(SqliteVecBackend(":memory:", dimension=64),
                 mock_embedder(dimension=64), chunk_size=1000, overlap=0)
    traj = TrajectoryStore(":memory:")
    harness = Harness(
        client=make_mock([text_turn("一"), text_turn("二")]), registry=ToolRegistry(),
        checkpoint_store=CheckpointStore(":memory:"), trajectory_store=traj,
        sink=TrajectorySink(traj), system_prompt="你是助手",
        memory=mem, memory_store=_CountSpy(25), memory_maintainer=m)
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None,
                    context_strategy="layered", context_enable_retrieval=True,
                    memory_consolidate_after=20)
    app = create_app(config=cfg, harness=harness, store=ConversationStore(":memory:"),
                     doc_store=DocumentStore(":memory:"))
    with TestClient(app) as client:           # 一个 portal 跨两次请求
        r = client.post("/api/auth/register", json={"username": "u", "password": "pw1234"})
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
        for _ in range(2):                    # 同一会话连问两轮
            with client.stream("POST", "/api/chat",
                               json={"conversation_id": cid, "message": "你好"},
                               headers=h) as resp:
                for _line in resp.iter_lines():
                    pass
        assert _wait(lambda: m.calls)
        time.sleep(0.3)                       # 给第二轮足够机会误起一个
        assert len(m.calls) == 1, "上一轮整合还在跑，第二轮不该再起"

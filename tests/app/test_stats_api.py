import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.config import AppConfig
from app.assembly import Harness
from app.conversations import ConversationStore
from app.documents import DocumentStore
from app.stats import StatsService
from harness.tools.base import ToolRegistry
from harness.persistence.checkpoint import CheckpointStore
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink


@pytest.fixture(autouse=True)
def _sqlite_allow_cross_thread(monkeypatch):
    original = sqlite3.connect

    def _patched(*args, **kwargs):
        kwargs.setdefault("check_same_thread", False)
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _patched)


def _cfg():
    return AppConfig(api_key="k", app_db_path=":memory:")


def _auth_headers(client, username="u"):
    r = client.post("/api/auth/register", json={"username": username, "password": "pw1234"})
    token = r.json()["token"]
    uid = client.app.state.auth.verify_token(token)[0]
    return {"Authorization": f"Bearer {token}"}, uid


def _traj_conn_with_run():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE trajectory_events(run_id TEXT, seq INTEGER, type TEXT, "
        "data TEXT, created_at TEXT, PRIMARY KEY(run_id, seq))")
    import json
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    rows = [
        ("r1", 0, "RunStarted", {"run_id": "r1"}),
        ("r1", 1, "ModelUsage", {"usage": {"prompt": 10, "completion": 5, "total": 15},
                                 "cost_usd": None, "attempts": 1, "latency_ms": 500.0}),
        ("r1", 2, "ToolStarted", {"tool_call": {"id": "c1", "name": "http_request", "arguments": {}}}),
        ("r1", 3, "RunFinished", {"message": {"role": "assistant", "content": "ok"}}),
    ]
    for rid, seq, typ, data in rows:
        conn.execute("INSERT INTO trajectory_events VALUES (?,?,?,?,?)",
                     (rid, seq, typ, json.dumps({"type": typ, "data": data}), ts))
    conn.commit()
    return conn


def _client():
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=None, registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj),
                      system_prompt="s")
    app_conn = sqlite3.connect(":memory:")
    store = ConversationStore(conn=app_conn)
    stats = StatsService(trajectory_conn=_traj_conn_with_run(), app_conn=app_conn, memory_conn=None)
    app = create_app(config=_cfg(), harness=harness,
                     store=store, doc_store=DocumentStore(conn=app_conn),
                     question_store=None, exam_store=None, wrong_store=None, quiz_service=None,
                     stats_service=stats)
    return TestClient(app)


def test_overview_requires_auth():
    client = _client()
    assert client.get("/api/stats/overview").status_code == 401


def test_overview_returns_both_sections():
    client = _client()
    headers, _uid = _auth_headers(client)
    r = client.get("/api/stats/overview", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["range_days"] == 14
    assert body["ops"]["totals"]["runs"] == 1
    assert body["ops"]["totals"]["total_tokens"] == 15
    assert {t["name"] for t in body["ops"]["tools"]} == {"http_request"}
    assert body["learn"]["effort"]["runs"] == 1
    assert any(a["label"] == "联网查资料" for a in body["learn"]["abilities"])


def test_overview_days_param_validated():
    client = _client()
    headers, _uid = _auth_headers(client)
    assert client.get("/api/stats/overview?days=7", headers=headers).json()["range_days"] == 7
    assert client.get("/api/stats/overview?days=0", headers=headers).status_code == 422
    assert client.get("/api/stats/overview?days=999", headers=headers).status_code == 422


class _FakeMemStore:
    def __init__(self, owner_by_id):
        self._o = dict(owner_by_id)
        self.deleted = []

    def get(self, ids):
        from types import SimpleNamespace
        return [SimpleNamespace(owner_id=self._o[i]) for i in ids if i in self._o]

    def delete(self, ids):
        self.deleted.extend(ids)
        for i in ids:
            self._o.pop(i, None)


def test_memory_list_and_delete_endpoint():
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=None, registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="s")
    from app.db import migrate
    app_conn = sqlite3.connect(":memory:")
    migrate(app_conn)                                  # 建 conversations 等表
    store = ConversationStore(conn=app_conn)
    mem_conn = sqlite3.connect(":memory:")
    mem_conn.execute("CREATE TABLE memory_records(id TEXT, owner_id TEXT, kind TEXT, "
                     "superseded INTEGER DEFAULT 0, text TEXT, created_at TEXT)")
    fake = _FakeMemStore({})
    stats = StatsService(trajectory_conn=_traj_conn_with_run(), app_conn=app_conn,
                         memory_conn=mem_conn, memory_store=fake)
    app = create_app(config=_cfg(), harness=harness, store=store,
                     doc_store=DocumentStore(conn=app_conn), question_store=None,
                     exam_store=None, wrong_store=None, quiz_service=None, stats_service=stats)
    client = TestClient(app)
    headers, uid = _auth_headers(client)

    conv_id = store.create(uid, "会话")                # 属于当前用户
    mem_conn.execute("INSERT INTO memory_records(id, owner_id, kind, text, created_at) "
                     "VALUES ('m1', ?, 'conversation', '记忆一', '2026-07-11T01:00:00+00:00')", (conv_id,))
    mem_conn.commit()
    fake._o["m1"] = conv_id

    items = client.get("/api/stats/memory", headers=headers).json()
    assert any(i["id"] == "m1" for i in items)         # 列表带 id

    assert client.delete("/api/stats/memory/m1", headers=headers).status_code == 200
    assert fake.deleted == ["m1"]                      # 走真实写后端删除
    # 不存在/他人的 → 404
    assert client.delete("/api/stats/memory/nope", headers=headers).status_code == 404
    # 需要鉴权
    assert client.delete("/api/stats/memory/m1").status_code == 401

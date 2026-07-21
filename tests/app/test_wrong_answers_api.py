"""/api/wrong-answers 列表：分页 + 题名/题型筛选（与 /api/questions 对齐）。"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.config import AppConfig
from app.assembly import Harness
from app.conversations import ConversationStore
from app.documents import DocumentStore
from app.questions import QuestionStore
from app.wrong_answers import WrongAnswerStore
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


def _app():
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=None, registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="s")
    ws = WrongAnswerStore(":memory:")
    app = create_app(config=AppConfig(api_key="k", app_db_path=":memory:", _env_file=None),
                     harness=harness, store=ConversationStore(":memory:"),
                     doc_store=DocumentStore(":memory:"), question_store=QuestionStore(":memory:"),
                     wrong_store=ws)
    return TestClient(app), ws


def _auth(client, username="u"):
    r = client.post("/api/auth/register", json={"username": username, "full_name": "测试用户", "password": "pw1234"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _snap(type, stem):
    return {"type": type, "stem": stem, "options": ["1", "2"], "answer": 1, "explanation": ""}


def _seed(client, ws, h):
    uid = client.app.state.auth.verify_token(h["Authorization"][7:])[0]
    for i in range(3):
        ws.create(uid, "q", "exam", _snap("single", f"光合作用{i}"), 0)
    ws.create(uid, "q", "exam", _snap("truefalse", "细胞呼吸判断"), 0)
    return uid


def test_list_returns_items_and_total():
    client, ws = _app()
    h = _auth(client)
    _seed(client, ws, h)
    body = client.get("/api/wrong-answers", headers=h).json()
    assert body["total"] == 4 and len(body["items"]) == 4


def test_list_paginated():
    client, ws = _app()
    h = _auth(client)
    _seed(client, ws, h)
    body = client.get("/api/wrong-answers?page=1&size=2", headers=h).json()
    assert body["total"] == 4 and len(body["items"]) == 2
    page2 = client.get("/api/wrong-answers?page=2&size=2", headers=h).json()
    assert len(page2["items"]) == 2
    # 两页不重叠
    assert {i["id"] for i in body["items"]}.isdisjoint({i["id"] for i in page2["items"]})


def test_list_filtered_by_type():
    client, ws = _app()
    h = _auth(client)
    _seed(client, ws, h)
    body = client.get("/api/wrong-answers?type=truefalse", headers=h).json()
    assert body["total"] == 1 and body["items"][0]["snapshot"]["stem"] == "细胞呼吸判断"


def test_list_filtered_by_stem_keyword():
    client, ws = _app()
    h = _auth(client)
    _seed(client, ws, h)
    body = client.get("/api/wrong-answers?q=光合", headers=h).json()
    assert body["total"] == 3
    assert all("光合" in i["snapshot"]["stem"] for i in body["items"])


def test_list_filter_isolated_per_user():
    client, ws = _app()
    h1 = _auth(client, "u1")
    _seed(client, ws, h1)
    h2 = _auth(client, "u2")
    body = client.get("/api/wrong-answers?q=光合", headers=h2).json()
    assert body["total"] == 0 and body["items"] == []

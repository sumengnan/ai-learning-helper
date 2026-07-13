import io
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


class _StubImporter:
    """按预设返回；记录收到的文件名。"""
    def __init__(self, result):
        self._result = result
        self.seen_filename = None

    async def import_text(self, user_id, filename, text):
        self.seen_filename = filename
        return self._result


def _app(question_importer=None):
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=None, registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="s")
    qs = QuestionStore(":memory:")
    app = create_app(config=AppConfig(api_key="k", app_db_path=":memory:"), harness=harness,
                     store=ConversationStore(":memory:"), doc_store=DocumentStore(":memory:"),
                     question_store=qs, wrong_store=WrongAnswerStore(":memory:"),
                     question_importer=question_importer)
    return TestClient(app), qs


def _auth(client, username="u"):
    r = client.post("/api/auth/register", json={"username": username, "password": "pw1234"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_generate_endpoint_removed():
    client, _ = _app()
    h = _auth(client)
    r = client.post("/api/questions/generate", json={"topic": "x", "count": 1}, headers=h)
    assert r.status_code in (404, 405)          # 端点已删


def test_list_paginated_and_filtered():
    client, qs = _app()
    h = _auth(client)
    uid = client.app.state.auth.verify_token(h["Authorization"][7:])[0]
    for i in range(3):
        qs.create(uid, {"type": "single", "stem": f"单选{i}", "options": ["1", "2"],
                        "answer": 1, "source": "算术"})
    qs.create(uid, {"type": "truefalse", "stem": "判断", "options": None, "answer": True,
                    "source": "常识"})
    body = client.get("/api/questions?page=1&size=2", headers=h).json()
    assert body["total"] == 4 and len(body["items"]) == 2
    only = client.get("/api/questions?type=truefalse", headers=h).json()
    assert only["total"] == 1 and only["items"][0]["stem"] == "判断"
    srcs = client.get("/api/questions/sources", headers=h).json()
    assert set(srcs) == {"算术", "常识"}


def test_import_endpoint_calls_importer():
    stub = _StubImporter({"imported": 3, "skipped_invalid": 0, "skipped_duplicate": 1})
    client, _ = _app(question_importer=stub)
    h = _auth(client)
    r = client.post("/api/questions/import", headers=h,
                    files={"file": ("题库.txt", io.BytesIO("单选题".encode()), "text/plain")})
    assert r.status_code == 200
    assert r.json()["imported"] == 3 and stub.seen_filename == "题库.txt"


def test_import_empty_file_400():
    stub = _StubImporter({"imported": 0, "skipped_invalid": 0, "skipped_duplicate": 0})
    client, _ = _app(question_importer=stub)
    h = _auth(client)
    r = client.post("/api/questions/import", headers=h,
                    files={"file": ("empty.txt", io.BytesIO(b"   "), "text/plain")})
    assert r.status_code == 400

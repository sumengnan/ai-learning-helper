import sqlite3
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.config import AppConfig
from app.assembly import Harness
from app.conversations import ConversationStore
from app.documents import DocumentStore
from app.downloads import DownloadStore
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
    return AppConfig(api_key="k", questions_db_path=":memory:",
                     exams_db_path=":memory:", wrong_answers_db_path=":memory:",
                     users_db_path=":memory:")


def _auth_headers(client, username="u"):
    r = client.post("/api/auth/register", json={"username": username, "password": "pw1234"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _client(tmp_path):
    dstore = DownloadStore(str(tmp_path / "dl"), ":memory:")
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=None, registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj),
                      system_prompt="s", download_store=dstore)
    app = create_app(config=_cfg(), harness=harness,
                     store=ConversationStore(":memory:"), doc_store=DocumentStore(":memory:"),
                     question_store=None, exam_store=None, wrong_store=None, quiz_service=None)
    # question/exam/wrong 传 None → create_app 内部用默认 :memory:（见 _cfg）
    return TestClient(app), dstore


def test_list_and_download(tmp_path):
    client, dstore = _client(tmp_path)
    h = _auth_headers(client)
    rec = dstore.create("hello.txt", b"hello world", "text/plain")
    listing = client.get("/api/downloads", headers=h).json()
    assert any(d["id"] == rec["id"] for d in listing)
    r = client.get(f"/api/downloads/{rec['id']}", headers=h)
    assert r.status_code == 200 and r.content == b"hello world"
    assert r.headers["content-type"].startswith("text/plain")


def test_download_404(tmp_path):
    client, _ = _client(tmp_path)
    h = _auth_headers(client)
    assert client.get("/api/downloads/nope", headers=h).status_code == 404


def test_download_404_when_disk_file_missing(tmp_path):
    # 登记在但磁盘文件被外部删 → 404（而非 FileResponse os.stat 抛 500）
    import os
    client, dstore = _client(tmp_path)
    h = _auth_headers(client)
    rec = dstore.create("x.txt", b"x", "text/plain")
    os.remove(dstore.path(rec["id"]))
    assert client.get(f"/api/downloads/{rec['id']}", headers=h).status_code == 404


def test_delete_removes_file_and_row(tmp_path):
    import os
    client, dstore = _client(tmp_path)
    h = _auth_headers(client)
    rec = dstore.create("x.txt", b"x", "text/plain")
    path = dstore.path(rec["id"])
    assert client.delete(f"/api/downloads/{rec['id']}", headers=h).status_code == 200
    assert not os.path.exists(path)
    assert client.get(f"/api/downloads/{rec['id']}", headers=h).status_code == 404
    assert client.delete(f"/api/downloads/{rec['id']}", headers=h).status_code == 404

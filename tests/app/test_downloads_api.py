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
    return AppConfig(api_key="k", app_db_path=":memory:")


def _auth_headers(client, username="u"):
    r = client.post("/api/auth/register", json={"username": username, "password": "pw1234"})
    token = r.json()["token"]
    uid = client.app.state.auth.verify_token(token)[0]
    return {"Authorization": f"Bearer {token}"}, uid


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
    h, uid = _auth_headers(client)
    rec = dstore.create(uid, "hello.txt", b"hello world", "text/plain")
    listing = client.get("/api/downloads", headers=h).json()
    assert any(d["id"] == rec["id"] for d in listing)
    r = client.get(f"/api/downloads/{rec['id']}", headers=h)
    assert r.status_code == 200 and r.content == b"hello world"
    assert r.headers["content-type"].startswith("text/plain")


def test_download_404(tmp_path):
    client, _ = _client(tmp_path)
    h, _ = _auth_headers(client)
    assert client.get("/api/downloads/nope", headers=h).status_code == 404


def test_download_404_when_disk_file_missing(tmp_path):
    # 登记在但磁盘文件被外部删 → 404（而非 FileResponse os.stat 抛 500）
    import os
    client, dstore = _client(tmp_path)
    h, uid = _auth_headers(client)
    rec = dstore.create(uid, "x.txt", b"x", "text/plain")
    os.remove(dstore.path(rec["id"]))
    assert client.get(f"/api/downloads/{rec['id']}", headers=h).status_code == 404


def test_delete_removes_file_and_row(tmp_path):
    import os
    client, dstore = _client(tmp_path)
    h, uid = _auth_headers(client)
    rec = dstore.create(uid, "x.txt", b"x", "text/plain")
    path = dstore.path(rec["id"])
    assert client.delete(f"/api/downloads/{rec['id']}", headers=h).status_code == 200
    assert not os.path.exists(path)
    assert client.get(f"/api/downloads/{rec['id']}", headers=h).status_code == 404
    assert client.delete(f"/api/downloads/{rec['id']}", headers=h).status_code == 404


def test_download_isolation_between_users(tmp_path):
    client, dstore = _client(tmp_path)
    ha, uida = _auth_headers(client, "downA")
    hb, _ = _auth_headers(client, "downB")
    rec = dstore.create(uida, "a.txt", "A 的文件".encode("utf-8"), "text/plain")
    # B 看不到、取不到、删不掉 A 的下载
    assert all(d["id"] != rec["id"] for d in client.get("/api/downloads", headers=hb).json())
    assert client.get(f"/api/downloads/{rec['id']}", headers=hb).status_code == 404
    assert client.delete(f"/api/downloads/{rec['id']}", headers=hb).status_code == 404
    # A 仍能取到
    assert client.get(f"/api/downloads/{rec['id']}", headers=ha).status_code == 200


def test_create_list_carry_conv_id(tmp_path):
    # 下载记录关联来源会话，供下载页跳转回聊天
    s = DownloadStore(str(tmp_path / "dl2"), ":memory:")
    rec = s.create("u1", "note.md", b"hi", "text/markdown", conv_id="c42")
    assert rec["conv_id"] == "c42"
    assert s.list("u1")[0]["conv_id"] == "c42"
    assert s.get("u1", rec["id"])["conv_id"] == "c42"
    # 默认无来源会话
    s.create("u1", "x.txt", b"y", "text/plain")
    assert s.list("u1")[0]["conv_id"] is None

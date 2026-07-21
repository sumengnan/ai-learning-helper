import io
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.config import AppConfig
from app.assembly import Harness
from app.attachments import AttachmentStore
from app.conversations import ConversationStore
from app.documents import DocumentStore
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


def _cfg(**kw):
    return AppConfig(api_key="k", app_db_path=":memory:", **kw)


def _auth_headers(client, username="u"):
    r = client.post("/api/auth/register", json={"username": username, "full_name": "测试用户", "password": "pw1234"})
    token = r.json()["token"]
    uid = client.app.state.auth.verify_token(token)[0]
    return {"Authorization": f"Bearer {token}"}, uid


def _client(tmp_path, client_obj=None, **cfg_kw):
    astore = AttachmentStore(str(tmp_path / "att"), ":memory:")
    store = ConversationStore(":memory:")
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=client_obj, registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="s")
    app = create_app(config=_cfg(**cfg_kw), harness=harness, store=store,
                     doc_store=DocumentStore(":memory:"), attachment_store=astore)
    return TestClient(app), store, astore


def _sse(resp):
    import json
    return [json.loads(l[6:]) for l in resp.iter_lines() if l and l.startswith("data: ")]


def _new_conv(client, h):
    return client.post("/api/conversations", json={"title": "t"}, headers=h).json()["id"]


def _upload(client, h, conv_id, name, data, ct="text/plain"):
    return client.post(f"/api/conversations/{conv_id}/attachments",
                       files={"file": (name, io.BytesIO(data), ct)}, headers=h)


def test_upload_preview_delete(tmp_path):
    client, _, _ = _client(tmp_path)
    h, _ = _auth_headers(client)
    conv = _new_conv(client, h)
    r = _upload(client, h, conv, "a.txt", b"hello", "text/plain")
    assert r.status_code == 200
    aid = r.json()["id"]
    # 预览：inline + 正确 content-type + 原字节
    g = client.get(f"/api/attachments/{aid}", headers=h)
    assert g.status_code == 200 and g.content == b"hello"
    assert g.headers["content-type"].startswith("text/plain")
    assert "inline" in g.headers.get("content-disposition", "")
    # 删除
    assert client.delete(f"/api/attachments/{aid}", headers=h).status_code == 200
    assert client.get(f"/api/attachments/{aid}", headers=h).status_code == 404


def test_upload_rejects_oversize(tmp_path):
    client, _, _ = _client(tmp_path, attachment_max_mb=1)
    h, _ = _auth_headers(client)
    conv = _new_conv(client, h)
    big = b"x" * (1024 * 1024 + 1)
    r = _upload(client, h, conv, "big.bin", big, "application/octet-stream")
    assert r.status_code == 413


def test_upload_rejects_over_count(tmp_path):
    client, _, _ = _client(tmp_path, attachment_max_count=2)
    h, _ = _auth_headers(client)
    conv = _new_conv(client, h)
    assert _upload(client, h, conv, "a.txt", b"a").status_code == 200
    assert _upload(client, h, conv, "b.txt", b"b").status_code == 200
    r = _upload(client, h, conv, "c.txt", b"c")
    assert r.status_code == 400


def test_upload_requires_conv_ownership(tmp_path):
    client, _, _ = _client(tmp_path)
    ha, _ = _auth_headers(client, "A")
    hb, _ = _auth_headers(client, "B")
    conv = _new_conv(client, ha)
    # B 不能往 A 的会话传附件
    assert _upload(client, hb, conv, "x.txt", b"x").status_code == 404


def test_preview_isolation_between_users(tmp_path):
    client, _, astore = _client(tmp_path)
    ha, uida = _auth_headers(client, "A")
    hb, _ = _auth_headers(client, "B")
    conv = _new_conv(client, ha)
    aid = _upload(client, ha, conv, "a.txt", b"secret").json()["id"]
    assert client.get(f"/api/attachments/{aid}", headers=hb).status_code == 404
    assert client.delete(f"/api/attachments/{aid}", headers=hb).status_code == 404
    assert client.get(f"/api/attachments/{aid}", headers=ha).status_code == 200


def test_preview_404_when_file_missing(tmp_path):
    client, _, astore = _client(tmp_path)
    h, _ = _auth_headers(client)
    conv = _new_conv(client, h)
    aid = _upload(client, h, conv, "a.txt", b"a").json()["id"]
    os.remove(astore.path(aid))
    assert client.get(f"/api/attachments/{aid}", headers=h).status_code == 404


def test_chat_with_attachment_persists_on_user_message(tmp_path, make_mock, text_turn):
    client, store, _ = _client(tmp_path, client_obj=make_mock([text_turn("好的")]))
    h, _ = _auth_headers(client)
    conv = _new_conv(client, h)
    aid = _upload(client, h, conv, "note.txt", b"hi", "text/plain").json()["id"]
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": conv, "message": "看看这个",
                             "attachment_ids": [aid]}, headers=h) as resp:
        assert resp.status_code == 200
        types = [e["type"] for e in _sse(resp)]
    assert "RunFinished" in types
    # ui_messages 的用户消息带 attachments，助手消息不带
    msgs = client.get(f"/api/conversations/{conv}/messages", headers=h).json()
    user = next(m for m in msgs if m["role"] == "user")
    assert user["attachments"] and user["attachments"][0]["id"] == aid
    assistant = next(m for m in msgs if m["role"] == "assistant")
    assert not assistant.get("attachments")
    # LLM 历史仍是原文（不含名单提示注入）
    assert store.messages(conv)[0].content == "看看这个"


def test_chat_rejects_unknown_attachment_id(tmp_path, make_mock, text_turn):
    client, _, _ = _client(tmp_path, client_obj=make_mock([text_turn("x")]))
    h, _ = _auth_headers(client)
    conv = _new_conv(client, h)
    r = client.post("/api/chat",
                    json={"conversation_id": conv, "message": "hi", "attachment_ids": ["nope"]},
                    headers=h)
    assert r.status_code == 404


def test_delete_conversation_cleans_attachments(tmp_path):
    client, _, astore = _client(tmp_path)
    h, uid = _auth_headers(client)
    conv = _new_conv(client, h)
    aid = _upload(client, h, conv, "a.txt", b"a").json()["id"]
    path = astore.path(aid)
    assert os.path.exists(path)
    assert client.delete(f"/api/conversations/{conv}", headers=h).status_code == 200
    assert not os.path.exists(path)
    assert astore.count_conv(uid, conv) == 0

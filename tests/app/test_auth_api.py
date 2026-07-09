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
from harness.tools.builtins.calculator import CalculatorTool


@pytest.fixture(autouse=True)
def _sqlite_allow_cross_thread(monkeypatch):
    original = sqlite3.connect

    def _patched(*args, **kwargs):
        kwargs.setdefault("check_same_thread", False)
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _patched)


def _client():
    reg = ToolRegistry(); reg.register(CalculatorTool())
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=None, registry=reg,
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="s")
    cfg = AppConfig(api_key="k", app_db_path=":memory:")
    app = create_app(config=cfg, harness=harness, store=ConversationStore(":memory:"),
                     doc_store=DocumentStore(":memory:"))
    return TestClient(app)


def _register(client, username, password="pw1234"):
    r = client.post("/api/auth/register", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


def test_register_returns_token_and_user():
    client = _client()
    r = client.post("/api/auth/register", json={"username": "alice", "password": "pw1234"})
    assert r.status_code == 200
    body = r.json()
    assert body["token"] and body["user"]["username"] == "alice"


def test_register_duplicate_400():
    client = _client()
    _register(client, "bob")
    r = client.post("/api/auth/register", json={"username": "bob", "password": "pw1234"})
    assert r.status_code == 400


def test_register_short_password_422():
    client = _client()
    r = client.post("/api/auth/register", json={"username": "x", "password": "123"})
    assert r.status_code == 422


def test_register_blank_username_422():
    client = _client()
    r = client.post("/api/auth/register", json={"username": "  ", "password": "pw1234"})
    assert r.status_code == 422


def test_login_ok_and_wrong_password():
    client = _client()
    _register(client, "carol")
    assert client.post("/api/auth/login",
                       json={"username": "carol", "password": "pw1234"}).status_code == 200
    r = client.post("/api/auth/login", json={"username": "carol", "password": "nope99"})
    assert r.status_code == 401


def test_me_requires_token():
    client = _client()
    token = _register(client, "dave")
    assert client.get("/api/auth/me").status_code == 401
    r = client.get("/api/auth/me", headers=_hdr(token))
    assert r.status_code == 200 and r.json()["username"] == "dave"


def test_protected_route_without_token_401():
    client = _client()
    assert client.get("/api/conversations").status_code == 401
    assert client.post("/api/conversations", json={}).status_code == 401


def test_bad_token_401():
    client = _client()
    assert client.get("/api/conversations", headers=_hdr("garbage.sig")).status_code == 401


def test_data_isolation_between_users():
    client = _client()
    ta = _register(client, "userA")
    tb = _register(client, "userB")
    cid = client.post("/api/conversations", json={"title": "A的对话"}, headers=_hdr(ta)).json()["id"]

    # A 能看到，B 看不到
    assert any(c["id"] == cid for c in client.get("/api/conversations", headers=_hdr(ta)).json())
    assert client.get("/api/conversations", headers=_hdr(tb)).json() == []

    # B 无法读取 A 的消息，也无法删除
    assert client.get(f"/api/conversations/{cid}/messages", headers=_hdr(tb)).status_code == 404
    client.delete(f"/api/conversations/{cid}", headers=_hdr(tb))
    assert client.get(f"/api/conversations/{cid}/messages", headers=_hdr(ta)).status_code == 200


def test_rename_conversation():
    client = _client()
    ta = _register(client, "renamer")
    cid = client.post("/api/conversations", json={}, headers=_hdr(ta)).json()["id"]
    assert client.patch(f"/api/conversations/{cid}",
                        json={"title": "新名字"}, headers=_hdr(ta)).status_code == 200
    got = client.get("/api/conversations", headers=_hdr(ta)).json()
    assert got[0]["title"] == "新名字"
    # 改别人的（不存在的）→ 404
    assert client.patch("/api/conversations/nope",
                        json={"title": "x"}, headers=_hdr(ta)).status_code == 404


def test_refresh_header_on_near_expiry(monkeypatch):
    # 直接构造快过期 token，验证 current_user 下发 X-Refresh-Token
    client = _client()
    token = _register(client, "renewer")
    auth = client.app.state.auth
    uid = auth.verify_token(token)[0]
    # 用短 ttl 的服务签发一个"剩余 < renew_within"的 token
    from app.auth import AuthService
    near = AuthService(auth.users, secret=auth._secret.decode(),
                       ttl=auth.renew_within() - 60)
    short_tok = near.issue_token(uid)
    r = client.get("/api/auth/me", headers=_hdr(short_tok))
    assert r.status_code == 200
    assert "X-Refresh-Token" in r.headers

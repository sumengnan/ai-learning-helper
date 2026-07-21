import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.conversations import ConversationStore
from app.documents import DocumentStore
from app.assembly import Harness
from app.main import create_app
from app.profile import ProfileStore, render_profile_block, sanitize
from harness.tools.base import ToolRegistry
from harness.persistence.checkpoint import CheckpointStore
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink


# ---------- ProfileStore ----------

def _store():
    return ProfileStore(":memory:")


def test_get_default_is_empty():
    assert _store().get("u1") == {
        "identity": "", "goal": "", "explain_prefs": [], "tone": "", "notes": ""}


def test_upsert_then_get_roundtrip():
    s = _store()
    out = s.upsert("u1", {"identity": "职场转行", "goal": "做 AI 产品",
                          "explain_prefs": ["多用类比", "步骤拆细"], "tone": "鼓励式",
                          "notes": "术语给英文"})
    assert out["identity"] == "职场转行" and out["explain_prefs"] == ["多用类比", "步骤拆细"]
    assert s.get("u1") == out                       # 落库可复原
    # 再次 upsert 覆盖（而非新增行）
    s.upsert("u1", {"identity": "改了", "explain_prefs": [], "goal": "", "tone": "", "notes": ""})
    got = s.get("u1")
    assert got["identity"] == "改了" and got["explain_prefs"] == []


def test_upsert_sanitizes_trim_limit_dedupe():
    s = _store()
    got = s.upsert("u1", {"identity": "  水平  ", "goal": "x" * 500,
                          "explain_prefs": ["a", "a", " b ", ""], "tone": "", "notes": "n"})
    assert got["identity"] == "水平"                 # 去首尾空白
    assert len(got["goal"]) == 200                   # 限长
    assert got["explain_prefs"] == ["a", "b"]        # 去重 + 去空 + trim


def test_profiles_isolated_by_user():
    s = _store()
    s.upsert("u1", {"identity": "甲", "goal": "", "explain_prefs": [], "tone": "", "notes": ""})
    assert s.get("u2")["identity"] == ""             # 别的用户看不到


# ---------- render_profile_block ----------

def test_render_empty_returns_blank():
    assert render_profile_block(None) == ""
    assert render_profile_block(sanitize({})) == ""
    assert render_profile_block({"identity": "  ", "explain_prefs": []}) == ""


def test_render_partial_only_nonempty_fields():
    out = render_profile_block({"identity": "初学者", "explain_prefs": ["多用类比"]})
    assert "<user_profile>" in out and "</user_profile>" in out
    assert "身份/水平：初学者" in out and "讲解偏好：多用类比" in out
    assert "学习目标" not in out and "语气" not in out    # 空字段不出现


def test_render_full_joins_prefs():
    out = render_profile_block({
        "identity": "i", "goal": "g", "explain_prefs": ["多用类比", "少代码"],
        "tone": "严格教练", "notes": "n"})
    assert "讲解偏好：多用类比、少代码" in out
    assert "语气：严格教练" in out and "其他要求：n" in out


# ---------- API ----------

def _auth(client, username="u"):
    r = client.post("/api/auth/register", json={"username": username, "full_name": "测试用户", "password": "pw1234"})
    token = r.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _client():
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=None, registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="s")
    app = create_app(config=AppConfig(api_key="k", app_db_path=":memory:"), harness=harness,
                     store=ConversationStore(":memory:"), doc_store=DocumentStore(":memory:"))
    return TestClient(app)


@pytest.fixture(autouse=True)
def _sqlite_cross_thread(monkeypatch):
    original = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect",
                        lambda *a, **k: original(*a, **{**k, "check_same_thread": False}))


def test_api_requires_auth():
    assert _client().get("/api/profile").status_code == 401


def test_api_get_default_then_put_roundtrip():
    client = _client()
    h = _auth(client)
    assert client.get("/api/profile", headers=h).json()["identity"] == ""
    r = client.put("/api/profile", headers=h, json={
        "identity": "职场人", "goal": "", "explain_prefs": ["步骤拆细"], "tone": "鼓励式", "notes": ""})
    assert r.status_code == 200 and r.json()["tone"] == "鼓励式"
    assert client.get("/api/profile", headers=h).json()["explain_prefs"] == ["步骤拆细"]


def test_api_isolated_between_users():
    client = _client()
    ha = _auth(client, "alice")
    hb = _auth(client, "bob")
    client.put("/api/profile", headers=ha, json={
        "identity": "alice的", "goal": "", "explain_prefs": [], "tone": "", "notes": ""})
    assert client.get("/api/profile", headers=hb).json()["identity"] == ""

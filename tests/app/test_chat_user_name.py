"""用户姓名进系统提示：让模型知道该怎么称呼当前用户。

这里盯的是**接线**——render_profile_block 的渲染逻辑由 test_profile.py 覆盖，
本文件确认 chat 路由真的把 user_store 里的姓名喂了进去。历史上这类"值取到了但
没接上"的断线不会报任何错，只会安静地少一段提示词。
"""
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


def _client(make_mock, turns):
    reg = ToolRegistry(); reg.register(CalculatorTool())
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=make_mock(turns), registry=reg,
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="你是助手")
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None)
    app = create_app(config=cfg, harness=harness, store=ConversationStore(":memory:"),
                     doc_store=DocumentStore(":memory:"))
    return TestClient(app)


def _register(client, name="张三"):
    r = client.post("/api/auth/register",
                    json={"username": "u", "full_name": name, "password": "pw1234"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _capture_profile_block(monkeypatch):
    """截下 chat 路由实际渲染出的 profile 块。"""
    import app.api.chat as chat
    seen = {}
    original = chat.render_profile_block

    def _spy(profile, full_name=""):
        seen["full_name"] = full_name
        seen["block"] = original(profile, full_name=full_name)
        return seen["block"]

    monkeypatch.setattr(chat, "render_profile_block", _spy)
    return seen


def _chat(client, headers, message="你好"):
    cid = client.post("/api/conversations", json={}, headers=headers).json()["id"]
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": message},
                       headers=headers) as resp:
        assert resp.status_code == 200
        for _ in resp.iter_lines():
            pass


def test_user_name_reaches_system_prompt(make_mock, text_turn, monkeypatch):
    client = _client(make_mock, [text_turn("好的")])
    headers = _register(client, "张三")
    seen = _capture_profile_block(monkeypatch)
    _chat(client, headers)
    assert seen["full_name"] == "张三"
    assert "- 姓名：张三" in seen["block"]


def test_legacy_account_without_name_changes_nothing(make_mock, text_turn, monkeypatch):
    """老账号 full_name 为空时行为与加这个特性之前一致：不多出空的姓名行。"""
    client = _client(make_mock, [text_turn("好的")])
    headers = _register(client, "张三")
    # 直接把库里的姓名抹掉，模拟迁移前建的老账号
    client.app.state.auth.users._db.execute("UPDATE users SET full_name=NULL")
    client.app.state.auth.users._db.commit()
    seen = _capture_profile_block(monkeypatch)
    _chat(client, headers)
    assert seen["full_name"] == ""
    assert "姓名" not in seen["block"]

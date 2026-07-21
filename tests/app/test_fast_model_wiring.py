"""快速模型档的接线：起标题 / 记忆提炼 是否真的用上了它。

单测 build_fast_completer 与 MemoryWriter 本身是不够的——把接线打回主模型，那些测试
照样全绿。这次改动的全部内容就是接线，故必须单独锁住。
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.assembly import Harness
from app.config import AppConfig
from app.conversations import ConversationStore
from app.documents import DocumentStore
from app.main import create_app
from harness.llm.base import StreamChunk
from harness.llm.openai_compat import get_extra_body_override
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


class _ThinkingSpy:
    """截下每次模型调用时实际生效的 extra_body 覆盖（即真会发出去的那份）。"""
    def __init__(self, reply: str = "标题") -> None:
        self.seen: list[dict] = []
        self._reply = reply

    async def stream(self, messages, tools):
        self.seen.append(dict(get_extra_body_override()))
        yield StreamChunk(type="text", text=self._reply)
        yield StreamChunk(type="done")


def _app(spy, **cfg_kw):
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=spy, registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj),
                      system_prompt="你是助手")
    cfg = AppConfig(api_key="k", model="qwen-max", app_db_path=":memory:",
                    _env_file=None, **cfg_kw)
    return create_app(config=cfg, harness=harness, store=ConversationStore(":memory:"),
                      doc_store=DocumentStore(":memory:"))


def _auth(client):
    r = client.post("/api/auth/register", json={"username": "u", "full_name": "测试用户", "password": "pw1234"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_autotitle_goes_through_fast_completer():
    """起标题必须走快速档：没配 FAST_MODEL 时也要显式关思考。

    此前它用的是不带任何 extra_body 的 build_completer —— 思考开不开由服务端默认决定。
    """
    spy = _ThinkingSpy()
    client = TestClient(_app(spy))
    h = _auth(client)
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    r = client.post(f"/api/conversations/{cid}/autotitle",
                    json={"message": "帮我制定一份 7 天的 AI 学习计划"}, headers=h)
    assert r.status_code == 200
    assert spy.seen, "起标题应发生一次模型调用"
    assert spy.seen[0].get("enable_thinking") is False


def test_autotitle_thinking_stays_off_under_ambient_toggle():
    """外层即使开着思考，起标题也恒关——快速档没有开关，也不受环境覆盖影响。

    对照组价值：证明上一条拿到的 False 是快速档显式发的，而不是「碰巧没人设过」。
    """
    from harness.llm.openai_compat import set_extra_body_override, reset_extra_body_override
    spy = _ThinkingSpy()
    client = TestClient(_app(spy))
    h = _auth(client)
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    tok = set_extra_body_override({"enable_thinking": True})
    try:
        client.post(f"/api/conversations/{cid}/autotitle",
                    json={"message": "你好"}, headers=h)
    finally:
        reset_extra_body_override(tok)
    assert spy.seen[0].get("enable_thinking") is False


def test_question_importer_wired_to_fast_completer(monkeypatch):
    """导入题目解析须接快速档：它是独立接口、够不着聊天页那个思考开关，
    不自己表态就一路跟着服务端默认思考（Qwen3 系默认开）。"""
    import app.main as M
    sentinel = object()
    # main.py 在模块顶层 import 了这个名字，故须打桩 app.main 上的绑定
    # （assembly.py 是在函数体内 import 的，那边打桩 app.completion 才生效）
    monkeypatch.setattr(M, "build_fast_completer", lambda client, cfg: sentinel)
    captured = {}
    real = M.QuestionImporter

    def _spy(complete, store, **kwargs):
        captured["complete"] = complete
        return real(complete, store, **kwargs)
    monkeypatch.setattr(M, "QuestionImporter", _spy)

    _app(_ThinkingSpy())          # create_app 内部装配 question_importer
    assert captured.get("complete") is sentinel

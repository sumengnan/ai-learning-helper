"""端到端：真实 /api/chat 端点里，active 考试答错 → 服务端确定性入错题集（模型只回一句话）。"""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.assembly import Harness
from app.main import create_app
from app.conversations import ConversationStore
from app.documents import DocumentStore
from app.exam_session import ExamSessionStore
from app.questions import QuestionStore
from app.wrong_answers import WrongAnswerStore
from harness.persistence.checkpoint import CheckpointStore
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink
from harness.tools.base import ToolRegistry
from harness.llm.base import StreamChunk


@pytest.fixture(autouse=True)
def _sqlite_allow_cross_thread(monkeypatch):
    orig = sqlite3.connect

    def _patched(*a, **k):
        k["check_same_thread"] = False
        return orig(*a, **k)
    monkeypatch.setattr(sqlite3, "connect", _patched)


def _harness(make_mock, turns):
    traj = TrajectoryStore(":memory:")
    return Harness(client=make_mock(turns), registry=ToolRegistry(),
                   checkpoint_store=CheckpointStore(":memory:"),
                   trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="你是助手")


def _app(make_mock, es, qs, ws):
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None)
    turn = [StreamChunk(type="text", text="好的。"), StreamChunk(type="done")]
    app = create_app(config=cfg, harness=_harness(make_mock, [turn]),
                     store=ConversationStore(":memory:"), doc_store=DocumentStore(":memory:"),
                     question_store=qs, wrong_store=ws, exam_session_store=es)
    return TestClient(app)


def _drain(resp):
    for line in resp.iter_lines():
        pass


class _ToolRecordingClient:
    """记录模型每轮实际收到的工具名，用于断言 save_wrong_answer 是否真的暴露给模型。"""

    def __init__(self, turns):
        self._turns = list(turns)
        self._i = 0
        self.seen_tools: list[list[str]] = []

    async def stream(self, messages, tools):
        self.seen_tools.append([t["function"]["name"] for t in tools])
        turn = self._turns[self._i]
        self._i += 1
        for chunk in turn:
            yield chunk


def _app_recording(es, qs, ws):
    turn = [StreamChunk(type="text", text="好的。"), StreamChunk(type="done")]
    rec = _ToolRecordingClient([turn])
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None)
    app = create_app(config=cfg, harness=_harness(lambda _t: rec, [turn]),
                     store=ConversationStore(":memory:"), doc_store=DocumentStore(":memory:"),
                     question_store=qs, wrong_store=ws, exam_session_store=es)
    return TestClient(app), rec


def _register(client):
    r = client.post("/api/auth/register", json={"username": "u", "password": "pw1234"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    return r.json()["user"]["id"], h, cid


def test_save_wrong_answer_tool_always_exposed_in_chat():
    """零散练习（无 active 考试）：save_wrong_answer 无条件暴露给模型，没有开关能藏掉它。"""
    client, rec = _app_recording(ExamSessionStore(":memory:"), QuestionStore(":memory:"),
                                 WrongAnswerStore(":memory:"))
    _uid, h, cid = _register(client)
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": "出道题考我"}, headers=h) as resp:
        _drain(resp)
    assert rec.seen_tools and "save_wrong_answer" in rec.seen_tools[0]


def test_save_wrong_answer_tool_hidden_during_active_exam():
    """考试进行中反而不暴露：判分与保存已由服务端确定性完成，暴露会导致重复入库。"""
    es = ExamSessionStore(":memory:")
    client, rec = _app_recording(es, QuestionStore(":memory:"), WrongAnswerStore(":memory:"))
    uid, h, cid = _register(client)
    es.start(uid, cid, [{"type": "single", "stem": "q", "options": ["a", "b"],
                         "answer": 1, "explanation": ""},
                        {"type": "single", "stem": "q2", "options": ["a", "b"],
                         "answer": 1, "explanation": ""}], "instant")
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": "A"}, headers=h) as resp:
        _drain(resp)
    assert rec.seen_tools and "save_wrong_answer" not in rec.seen_tools[0]


def test_active_exam_wrong_answer_saved_via_endpoint(make_mock):
    qs = QuestionStore(":memory:"); ws = WrongAnswerStore(":memory:"); es = ExamSessionStore(":memory:")
    client = _app(make_mock, es, qs, ws)
    r = client.post("/api/auth/register", json={"username": "u", "password": "pw1234"})
    uid = r.json()["user"]["id"]
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]

    # 直接种一场 active 即时考试（绕过 start_exam，专测判分中间件）
    es.start(uid, cid, [{"type": "single", "stem": "光合作用在哪?", "options": ["线粒体", "叶绿体"],
                         "answer": 1, "explanation": "叶绿体"}], "instant")

    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": "A"},
                       headers=h) as resp:
        assert resp.status_code == 200
        _drain(resp)

    # 模型只回了「好的。」，从未调用 save_wrong_answer——错题仍被服务端确定性写入
    rows = ws.list(uid)
    assert len(rows) == 1
    assert rows[0]["snapshot"]["stem"] == "光合作用在哪?" and rows[0]["user_answer"] == 0
    # 会话状态推进：唯一一题答完 → 考试结束
    assert es.get_active(uid, cid) is None


def test_stale_client_save_wrong_false_is_ignored(make_mock):
    """save_wrong 开关已移除：老前端仍发 save_wrong=false 时该字段被忽略，答错照样入库。"""
    qs = QuestionStore(":memory:"); ws = WrongAnswerStore(":memory:"); es = ExamSessionStore(":memory:")
    client = _app(make_mock, es, qs, ws)
    r = client.post("/api/auth/register", json={"username": "u", "password": "pw1234"})
    uid = r.json()["user"]["id"]
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    es.start(uid, cid, [{"type": "single", "stem": "q", "options": ["a", "b"],
                         "answer": 1, "explanation": ""}], "instant")
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": "A", "save_wrong": False},
                       headers=h) as resp:
        assert resp.status_code == 200               # 多余字段不致 422
        _drain(resp)
    assert len(ws.list(uid)) == 1                    # 无视该字段，答错必存

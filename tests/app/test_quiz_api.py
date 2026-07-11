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
from app.quiz_service import QuizService
from harness.tools.base import ToolRegistry
from harness.persistence.checkpoint import CheckpointStore
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink
from harness.memory.memory import Memory
from harness.memory.sqlite_backend import SqliteVecBackend


@pytest.fixture(autouse=True)
def _sqlite_allow_cross_thread(monkeypatch):
    original = sqlite3.connect

    def _patched(*args, **kwargs):
        kwargs.setdefault("check_same_thread", False)
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _patched)


GEN_JSON = ('[{"type":"single","stem":"光合作用在哪?","options":["线粒体","叶绿体"],'
            '"answer":1,"explanation":"叶绿体"},'
            '{"type":"short","stem":"简述光合作用","options":null,'
            '"answer":"光能转化为化学能","explanation":"要点"}]')


def _app(make_mock, mock_embedder, with_memory=True, complete=None):
    mstore = SqliteVecBackend(":memory:", dimension=64)
    mem = Memory(mstore, mock_embedder(dimension=64), 1000, 0) if with_memory else None
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=make_mock([]), registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj),
                      system_prompt="s", memory=mem,
                      memory_store=mstore if with_memory else None)
    qs = QuestionStore(":memory:")
    quiz = QuizService(mem, qs, complete or (lambda s, u: None)) if mem is not None else None
    app = create_app(config=AppConfig(api_key="k", app_db_path=":memory:"), harness=harness,
                     store=ConversationStore(":memory:"), doc_store=DocumentStore(":memory:"),
                     question_store=qs, wrong_store=WrongAnswerStore(":memory:"),
                     quiz_service=quiz)
    return TestClient(app), qs, mem


def _auth(client, username="u"):
    r = client.post("/api/auth/register", json={"username": username, "password": "pw1234"})
    token = r.json()["token"]
    uid = client.app.state.auth.verify_token(token)[0]
    return {"Authorization": f"Bearer {token}"}, uid


def test_generate_503_without_memory(make_mock, mock_embedder):
    client, _, _ = _app(make_mock, mock_embedder, with_memory=False)
    h, _ = _auth(client)
    assert client.post("/api/questions/generate",
                       json={"topic": "x", "count": 1}, headers=h).status_code == 503


def test_generate_422_without_knowledge(make_mock, mock_embedder):
    async def complete(s, u): return GEN_JSON
    client, _, _ = _app(make_mock, mock_embedder, complete=complete)
    h, _ = _auth(client)
    assert client.post("/api/questions/generate",
                       json={"topic": "空", "count": 1, "types": ["single"]}, headers=h).status_code == 422


@pytest.mark.asyncio
async def test_generate_502_on_bad_llm_output(make_mock, mock_embedder):
    async def complete(s, u): return "这不是 JSON"      # 触发 QuizError
    client, _, mem = _app(make_mock, mock_embedder, complete=complete)
    h, uid = _auth(client)
    await mem.add_texts(["有内容可检索"], f"knowledge:{uid}", {})
    r = client.post("/api/questions/generate",
                    json={"topic": "主题", "count": 1, "types": ["single"]}, headers=h)
    assert r.status_code == 502


# 注：模拟考试的组卷/判分/错题保存已从独立 /api/exams 端点迁移到聊天工具，
# 相关覆盖见 tests/app/test_exam_tools.py。此处仅保留出题（题库）相关端点测试。

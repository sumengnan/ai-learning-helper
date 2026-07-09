import sqlite3
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.config import AppConfig
from app.assembly import Harness
from app.conversations import ConversationStore
from app.documents import DocumentStore
from app.questions import QuestionStore
from app.exams import ExamStore
from app.wrong_answers import WrongAnswerStore
from app.quiz_service import QuizService
from harness.tools.base import ToolRegistry
from harness.persistence.checkpoint import CheckpointStore
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink
from harness.memory.memory import Memory
from harness.memory.store import MemoryStore


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
    mstore = MemoryStore(":memory:", dimension=64)
    mem = Memory(mstore, mock_embedder(dimension=64), 1000, 0) if with_memory else None
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=make_mock([]), registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj),
                      system_prompt="s", memory=mem,
                      memory_store=mstore if with_memory else None)
    qs = QuestionStore(":memory:")
    quiz = QuizService(mem, qs, complete or (lambda s, u: None)) if mem is not None else None
    app = create_app(config=AppConfig(api_key="k", users_db_path=":memory:"), harness=harness,
                     store=ConversationStore(":memory:"), doc_store=DocumentStore(":memory:"),
                     question_store=qs, exam_store=ExamStore(":memory:"),
                     wrong_store=WrongAnswerStore(":memory:"), quiz_service=quiz)
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


@pytest.mark.asyncio
async def test_full_quiz_flow(make_mock, mock_embedder):
    async def complete(s, u):
        # generate 用 GEN_JSON；grade(short) 用打分 JSON。按提示内容区分。
        # 按 system_prompt 区分出题/判分（GEN_SYSTEM 含「出题」，GRADE_SYSTEM 含「阅卷」），
        # 比按 user prompt 关键字更稳，不受题面文案影响。
        return GEN_JSON if "出题" in s else '{"score": 90, "feedback": "好"}'
    client, qs, mem = _app(make_mock, mock_embedder, complete=complete)
    h, uid = _auth(client)
    await mem.add_texts(["光合作用在叶绿体进行，把光能转化为化学能"], f"knowledge:{uid}", {})

    # 出题
    r = client.post("/api/questions/generate",
                    json={"topic": "光合作用", "count": 2, "types": ["single", "short"]}, headers=h)
    assert r.status_code == 200
    qlist = client.get("/api/questions", headers=h).json()
    assert len(qlist) == 2

    # 组卷不含答案
    paper = client.post("/api/exams", json={"count": 2}, headers=h).json()["questions"]
    assert paper and all("answer" not in q and "explanation" not in q for q in paper)

    # 交卷：单选答对(1)、简答走 LLM 判 90 分→对
    single = next(q for q in qlist if q["type"] == "single")
    short = next(q for q in qlist if q["type"] == "short")
    submit = client.post("/api/exams/submit", json={"answers": [
        {"question_id": single["id"], "user_answer": 1},
        {"question_id": short["id"], "user_answer": "光能变化学能"}]}, headers=h).json()
    assert submit["total"] == 2 and submit["correct"] == 2
    assert client.get("/api/exams", headers=h).json()[0]["id"] == submit["exam_id"]


@pytest.mark.asyncio
async def test_wrong_answer_survives_question_delete(make_mock, mock_embedder):
    async def complete(s, u):
        return GEN_JSON if "出题" in s else '{"score": 10, "feedback": "错"}'
    client, qs, mem = _app(make_mock, mock_embedder, complete=complete)
    h, uid = _auth(client)
    await mem.add_texts(["光合作用内容"], f"knowledge:{uid}", {})
    # 只限定 single 题型：GEN_JSON 中的 short 题会被 _valid 按 types 过滤掉，
    # 题库最终只有这一道单选题，便于验证「删题后题库应为空」。
    client.post("/api/questions/generate",
                json={"topic": "光合作用", "count": 2, "types": ["single"]}, headers=h)
    qlist = client.get("/api/questions", headers=h).json()
    single = next(q for q in qlist if q["type"] == "single")
    # 单选故意答错 → 进错题集
    client.post("/api/exams/submit",
                json={"answers": [{"question_id": single["id"], "user_answer": 0}]}, headers=h)
    wrong = client.get("/api/wrong-answers", headers=h).json()
    assert len(wrong) == 1 and wrong[0]["snapshot"]["stem"]
    # 删原题后，错题快照仍在
    client.delete(f"/api/questions/{single['id']}", headers=h)
    assert client.get("/api/questions", headers=h).json() == []
    still = client.get("/api/wrong-answers", headers=h).json()
    assert len(still) == 1 and still[0]["snapshot"]["stem"]
    # 批量删错题
    client.post("/api/wrong-answers/delete", json={"ids": [w["id"] for w in still]}, headers=h)
    assert client.get("/api/wrong-answers", headers=h).json() == []

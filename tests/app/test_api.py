import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.config import AppConfig
from app.assembly import Harness
from app.conversations import ConversationStore
from app.documents import DocumentStore
from harness.tools.base import ToolRegistry
from harness.tools.builtins.calculator import CalculatorTool
from harness.persistence.checkpoint import CheckpointStore
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink


@pytest.fixture(autouse=True)
def _sqlite_allow_cross_thread(monkeypatch):
    # fastapi TestClient 用 anyio 的 blocking portal 在独立线程里跑 ASGI app，
    # 与本测试构造 CheckpointStore/TrajectoryStore 的主线程不同；harness 这两个
    # store 用 sqlite3.connect() 默认 check_same_thread=True，跨线程访问会抛
    # ProgrammingError。这里仅为测试放宽该限制（不改 harness 代码），生产环境
    # uvicorn 单事件循环线程内不会触发此问题。
    original_connect = sqlite3.connect

    def _patched_connect(*args, **kwargs):
        kwargs.setdefault("check_same_thread", False)
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _patched_connect)


def _fake_harness(make_mock, turns):
    reg = ToolRegistry(); reg.register(CalculatorTool())
    traj = TrajectoryStore(":memory:")
    return Harness(client=make_mock(turns), registry=reg,
                   checkpoint_store=CheckpointStore(":memory:"),
                   trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="你是助手")


def _cfg():
    # quiz 三个 store 未在这些测试里注入，走 create_app 默认路径；用 :memory: 免得
    # 在 cwd 落下 questions.db/exams.db/wrong_answers.db。
    return AppConfig(api_key="k", questions_db_path=":memory:",
                     exams_db_path=":memory:", wrong_answers_db_path=":memory:")


def _client(make_mock, turns=None):
    store = ConversationStore(":memory:")
    app = create_app(config=_cfg(),
                     harness=_fake_harness(make_mock, turns or []), store=store,
                     doc_store=DocumentStore(":memory:"))
    return TestClient(app), store


def _client_with_kb(make_mock, mock_embedder):
    from harness.memory.memory import Memory
    from harness.memory.store import MemoryStore
    from app.documents import DocumentStore
    mstore = MemoryStore(":memory:", dimension=64)
    mem = Memory(mstore, mock_embedder(dimension=64), 1000, 0)
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=make_mock([]), registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="s",
                      memory=mem, memory_store=mstore)
    store = ConversationStore(":memory:")
    doc_store = DocumentStore(":memory:")
    app = create_app(config=_cfg(), harness=harness, store=store, doc_store=doc_store)
    return TestClient(app)


def test_upload_list_delete(make_mock, mock_embedder):
    client = _client_with_kb(make_mock, mock_embedder)
    r = client.post("/api/documents", files={"file": ("bio.txt", "光合作用内容".encode(), "text/plain")})
    assert r.status_code == 200
    doc_id = r.json()["id"]
    assert any(d["id"] == doc_id for d in client.get("/api/documents").json())
    assert client.delete(f"/api/documents/{doc_id}").status_code == 200
    assert client.get("/api/documents").json() == []


def test_upload_unsupported_400(make_mock, mock_embedder):
    client = _client_with_kb(make_mock, mock_embedder)
    r = client.post("/api/documents", files={"file": ("x.pptx", b"data", "application/octet-stream")})
    assert r.status_code == 400


def test_upload_oversize_413(make_mock, mock_embedder):
    client = _client_with_kb(make_mock, mock_embedder)
    # AppConfig 默认 app_max_upload_mb=20；构造 >20MB 的假文件
    big = b"x" * (21 * 1024 * 1024)
    r = client.post("/api/documents", files={"file": ("big.txt", big, "text/plain")})
    assert r.status_code == 413


def test_upload_corrupt_pdf_400(make_mock, mock_embedder):
    client = _client_with_kb(make_mock, mock_embedder)
    r = client.post("/api/documents", files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert r.status_code == 400


def test_upload_503_without_memory(make_mock):
    # 默认 _client（无 memory 的 harness）→ 上传 503
    client, _ = _client(make_mock, [])
    r = client.post("/api/documents", files={"file": ("a.txt", b"hi", "text/plain")})
    assert r.status_code == 503


def test_conversation_crud(make_mock):
    client, _ = _client(make_mock)
    cid = client.post("/api/conversations", json={"title": "T"}).json()["id"]
    assert any(c["id"] == cid for c in client.get("/api/conversations").json())
    assert client.get(f"/api/conversations/{cid}/messages").json() == []
    client.delete(f"/api/conversations/{cid}")
    assert client.get(f"/api/conversations/{cid}/messages").status_code == 404


def _sse_events(resp):
    events = []
    for line in resp.iter_lines():
        if line and line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def test_chat_streams_sse_and_persists(make_mock, text_turn):
    client, store = _client(make_mock, [text_turn("你好呀")])
    cid = client.post("/api/conversations", json={}).json()["id"]
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": "hi"}) as resp:
        assert resp.status_code == 200
        types = [e["type"] for e in _sse_events(resp)]
    assert "TextDelta" in types and "RunFinished" in types
    msgs = [m.content for m in store.messages(cid)]
    assert msgs == ["hi", "你好呀"]                     # 用户问 + 最终答落库


def test_chat_tool_call_in_stream(make_mock, text_turn, tool_turn):
    turns = [tool_turn("calculator", '{"expression":"(12+8)*3"}', call_id="c1"),
             text_turn("答案是 60")]
    client, store = _client(make_mock, turns)
    cid = client.post("/api/conversations", json={}).json()["id"]
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": "算 (12+8)*3"}) as resp:
        events = _sse_events(resp)
    tfs = [e for e in events if e["type"] == "ToolFinished"]
    assert tfs and tfs[0]["data"]["result"]["content"] == "60"


def test_two_turns_accumulate_history(make_mock, text_turn):
    client, store = _client(make_mock, [text_turn("答1"), text_turn("答2")])
    cid = client.post("/api/conversations", json={}).json()["id"]
    for msg in ["问1", "问2"]:
        with client.stream("POST", "/api/chat",
                           json={"conversation_id": cid, "message": msg}) as resp:
            _sse_events(resp)
    assert [m.content for m in store.messages(cid)] == ["问1", "答1", "问2", "答2"]


def test_chat_unknown_conversation_404(make_mock, text_turn):
    client, _ = _client(make_mock, [text_turn("x")])
    resp = client.post("/api/chat", json={"conversation_id": "nope", "message": "hi"})
    assert resp.status_code == 404


class _BoomClient:
    """stream 一开就抛非瞬时异常，让 loop 产出 RunError。"""

    async def stream(self, messages, tools):
        raise RuntimeError("boom")
        yield  # 让其成为异步生成器（永不到达）


def test_chat_run_error_persists_clean_message():
    # 即便本轮出错（RunError），也应把用户消息 + 干净提示落库，而非泄漏原始错误
    reg = ToolRegistry(); reg.register(CalculatorTool())
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=_BoomClient(), registry=reg,
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="你是助手")
    store = ConversationStore(":memory:")
    app = create_app(config=_cfg(), harness=harness, store=store,
                     doc_store=DocumentStore(":memory:"))
    client = TestClient(app)
    cid = client.post("/api/conversations", json={}).json()["id"]
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": "hi"}) as resp:
        types = [e["type"] for e in _sse_events(resp)]
    assert "RunError" in types
    assert [m.content for m in store.messages(cid)] == ["hi", "（本轮未能完成，请重试）"]

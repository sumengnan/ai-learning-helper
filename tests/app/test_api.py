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
from harness.llm.base import StreamChunk


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
    # quiz/user 等 store 未在这些测试里注入，走 create_app 默认路径；用 :memory: 免得
    # 在 cwd 落下 app.db。
    # _env_file=None：测试隔离，不读开发机 .env（否则 answer_gate/judge_model 等会污染用例）
    return AppConfig(api_key="k", app_db_path=":memory:", _env_file=None)


def _auth_headers(client, username="u"):
    r = client.post("/api/auth/register", json={"username": username, "password": "pw1234"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _client(make_mock, turns=None):
    store = ConversationStore(":memory:")
    app = create_app(config=_cfg(),
                     harness=_fake_harness(make_mock, turns or []), store=store,
                     doc_store=DocumentStore(":memory:"))
    return TestClient(app), store


def _client_with_kb(make_mock, mock_embedder):
    from harness.memory.memory import Memory
    from harness.memory.sqlite_backend import SqliteVecBackend
    from app.documents import DocumentStore
    mstore = SqliteVecBackend(":memory:", dimension=64)
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


def test_upload_list_delete_fragments(make_mock, mock_embedder):
    # 知识库以片段（chunk）为展示单元：上传后列出片段、删除单个片段
    client = _client_with_kb(make_mock, mock_embedder)
    h = _auth_headers(client)
    r = client.post("/api/documents", files={"file": ("bio.txt", "光合作用内容".encode(), "text/plain")}, headers=h)
    assert r.status_code == 200
    listed = client.get("/api/documents", headers=h).json()
    assert listed["total"] >= 1 and "total_chunks" not in listed
    frag = listed["items"][0]
    assert frag["filename"] == "bio.txt" and frag["category"] == "文本"   # 来源文件名 + 分类
    assert "光合作用" in frag["excerpt"]                                    # 卡片正文是片段内容
    # 逐个删除片段直至清空，验证同步
    for f in listed["items"]:
        assert client.delete(f"/api/documents/{f['id']}", headers=h).status_code == 200
    empty = client.get("/api/documents", headers=h).json()
    assert empty["items"] == [] and empty["total"] == 0
    # 删不存在的片段 → 404
    assert client.delete("/api/documents/nope", headers=h).status_code == 404


def test_get_fragment_detail(make_mock, mock_embedder):
    client = _client_with_kb(make_mock, mock_embedder)
    h = _auth_headers(client)
    client.post("/api/documents", files={"file": ("bio.txt", "光合作用完整内容".encode(), "text/plain")}, headers=h)
    frag_id = client.get("/api/documents", headers=h).json()["items"][0]["id"]
    detail = client.get(f"/api/documents/{frag_id}", headers=h).json()
    assert detail["id"] == frag_id and detail["filename"] == "bio.txt"
    assert detail["category"] == "文本" and "光合作用完整内容" in detail["text"]
    # 不存在的片段 → 404
    assert client.get("/api/documents/nope", headers=h).status_code == 404


def test_search_documents(make_mock, mock_embedder):
    client = _client_with_kb(make_mock, mock_embedder)
    h = _auth_headers(client)
    client.post("/api/documents", files={"file": ("bio.txt", "光合作用内容".encode(), "text/plain")}, headers=h)
    hits = client.get("/api/documents/search", params={"q": "光合作用"}, headers=h).json()
    assert len(hits) >= 1
    top = hits[0]
    assert 0 <= top["relevance"] <= 100 and top["category"] == "文本"
    # 空查询返回空
    assert client.get("/api/documents/search", params={"q": "  "}, headers=h).json() == []


def test_upload_unsupported_400(make_mock, mock_embedder):
    client = _client_with_kb(make_mock, mock_embedder)
    h = _auth_headers(client)
    r = client.post("/api/documents", files={"file": ("x.pptx", b"data", "application/octet-stream")}, headers=h)
    assert r.status_code == 400


def test_upload_oversize_413(make_mock, mock_embedder):
    client = _client_with_kb(make_mock, mock_embedder)
    h = _auth_headers(client)
    # AppConfig 默认 app_max_upload_mb=20；构造 >20MB 的假文件
    big = b"x" * (21 * 1024 * 1024)
    r = client.post("/api/documents", files={"file": ("big.txt", big, "text/plain")}, headers=h)
    assert r.status_code == 413


def test_upload_corrupt_pdf_400(make_mock, mock_embedder):
    client = _client_with_kb(make_mock, mock_embedder)
    h = _auth_headers(client)
    r = client.post("/api/documents", files={"file": ("x.pdf", b"not a pdf", "application/pdf")}, headers=h)
    assert r.status_code == 400


def test_upload_503_without_memory(make_mock):
    # 默认 _client（无 memory 的 harness）→ 上传 503
    client, _ = _client(make_mock, [])
    h = _auth_headers(client)
    r = client.post("/api/documents", files={"file": ("a.txt", b"hi", "text/plain")}, headers=h)
    assert r.status_code == 503


def test_conversation_crud(make_mock):
    client, _ = _client(make_mock)
    h = _auth_headers(client)
    cid = client.post("/api/conversations", json={"title": "T"}, headers=h).json()["id"]
    assert any(c["id"] == cid for c in client.get("/api/conversations", headers=h).json())
    assert client.get(f"/api/conversations/{cid}/messages", headers=h).json() == []
    client.delete(f"/api/conversations/{cid}", headers=h)
    assert client.get(f"/api/conversations/{cid}/messages", headers=h).status_code == 404


class _FakeSandboxManager:
    """记录 destroy 调用；提供 create_app 启动/关停会用到的钩子。"""

    def __init__(self):
        self.destroyed = []

    def sweep_orphans(self):        # 启动清扫：测试里 no-op
        pass

    async def destroy(self, conv_id):
        self.destroyed.append(conv_id)

    async def close_all(self):
        pass


def test_delete_conversation_cascades_runs_and_sandbox(make_mock, text_turn):
    from harness.state import RunState

    reg = ToolRegistry(); reg.register(CalculatorTool())
    traj = TrajectoryStore(":memory:")
    ckpt = CheckpointStore(":memory:")
    mgr = _FakeSandboxManager()
    harness = Harness(client=make_mock([text_turn("答")]), registry=reg,
                      checkpoint_store=ckpt, trajectory_store=traj,
                      sink=TrajectorySink(traj), system_prompt="s",
                      sandbox_manager=mgr)
    store = ConversationStore(":memory:")
    app = create_app(config=_cfg(), harness=harness, store=store,
                     doc_store=DocumentStore(":memory:"))
    client = TestClient(app)
    h = _auth_headers(client)
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]

    # 注册生成的是 user_id（非用户名），run_ids 带归属校验需真实 user_id
    uid = store._conn.execute(
        "SELECT user_id FROM conversations WHERE id=?", (cid,)).fetchone()[0]

    # 跑一轮 → 自动登记 run 映射并写入轨迹；再手动为该 run 存一个检查点
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": "hi"}, headers=h) as resp:
        _sse_events(resp)
    run_ids = store.run_ids(uid, cid)
    # 一轮登记两个 run：turn 句柄（RunManager/接回用）+ 内部 loop run（trajectory 挂它）
    assert len(run_ids) == 2
    rid = next(r for r in run_ids if traj.load(r))   # 带轨迹的那个（内部 loop run）
    ckpt.save(RunState(run_id=rid))
    assert traj.load(rid) != [] and ckpt.load(rid) is not None

    # 删除会话 → 清理检查点/轨迹，并销毁该会话沙箱
    assert client.delete(f"/api/conversations/{cid}", headers=h).status_code == 200
    assert traj.load(rid) == []
    assert ckpt.load(rid) is None
    assert store.run_ids(uid, cid) == []
    assert mgr.destroyed == [cid]


def _sse_events(resp):
    events = []
    for line in resp.iter_lines():
        if line and line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def test_chat_streams_sse_and_persists(make_mock, text_turn):
    client, store = _client(make_mock, [text_turn("你好呀")])
    h = _auth_headers(client)
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": "hi"}, headers=h) as resp:
        assert resp.status_code == 200
        types = [e["type"] for e in _sse_events(resp)]
    assert "TextDelta" in types and "RunFinished" in types
    msgs = [m.content for m in store.messages(cid)]
    assert msgs == ["hi", "你好呀"]                     # 用户问 + 最终答落库


def test_chat_tool_call_in_stream(make_mock, text_turn, tool_turn):
    turns = [tool_turn("calculator", '{"expression":"(12+8)*3"}', call_id="c1"),
             text_turn("答案是 60")]
    client, store = _client(make_mock, turns)
    h = _auth_headers(client)
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": "算 (12+8)*3"}, headers=h) as resp:
        events = _sse_events(resp)
    tfs = [e for e in events if e["type"] == "ToolFinished"]
    assert tfs and tfs[0]["data"]["result"]["content"] == "60"
    # 工具调用轨迹落库：切换对话回来后 get_messages 仍能还原 steps
    msgs = client.get(f"/api/conversations/{cid}/messages", headers=h).json()
    assistant = next(m for m in msgs if m["role"] == "assistant")
    assert assistant["steps"] == [
        {"tool": "calculator", "args": {"expression": "(12+8)*3"},
         "result": "60", "is_error": False}]


def test_two_turns_accumulate_history(make_mock, text_turn):
    client, store = _client(make_mock, [text_turn("答1"), text_turn("答2")])
    h = _auth_headers(client)
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    for msg in ["问1", "问2"]:
        with client.stream("POST", "/api/chat",
                           json={"conversation_id": cid, "message": msg}, headers=h) as resp:
            _sse_events(resp)
    assert [m.content for m in store.messages(cid)] == ["问1", "答1", "问2", "答2"]


def test_chat_unknown_conversation_404(make_mock, text_turn):
    client, _ = _client(make_mock, [text_turn("x")])
    h = _auth_headers(client)
    resp = client.post("/api/chat", json={"conversation_id": "nope", "message": "hi"}, headers=h)
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
    h = _auth_headers(client)
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": "hi"}, headers=h) as resp:
        types = [e["type"] for e in _sse_events(resp)]
    assert "RunError" in types
    assert [m.content for m in store.messages(cid)] == ["hi", "（本轮未能完成，请重试）"]


class _PartialThenBoomClient:
    """先流式输出一段文本，再抛错——模拟「输出到一半失败」。"""

    async def stream(self, messages, tools):
        yield StreamChunk(type="text", text="已经生成的部分答案")
        raise RuntimeError("boom")


def test_chat_run_error_keeps_partial_output():
    # 出错时已流式输出的内容应保留，干净提示拼在其后（不只显示提示）
    reg = ToolRegistry(); reg.register(CalculatorTool())
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=_PartialThenBoomClient(), registry=reg,
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="你是助手")
    store = ConversationStore(":memory:")
    app = create_app(config=_cfg(), harness=harness, store=store,
                     doc_store=DocumentStore(":memory:"))
    client = TestClient(app)
    h = _auth_headers(client)
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": "hi"}, headers=h) as resp:
        _sse_events(resp)
    msgs = [m.content for m in store.messages(cid)]
    assert msgs == ["hi", "已经生成的部分答案\n\n（本轮未能完成，请重试）"]


def test_delete_question_cascades_related_wrong_answers(make_mock):
    # 删题时若错题集有对应错题：未确认(force)先不删并回报数量；确认后连带删掉错题。
    from app.questions import QuestionStore
    from app.wrong_answers import WrongAnswerStore
    qs = QuestionStore(":memory:")
    ws = WrongAnswerStore(":memory:")
    app = create_app(config=_cfg(), harness=_fake_harness(make_mock, []),
                     store=ConversationStore(":memory:"), doc_store=DocumentStore(":memory:"),
                     question_store=qs, wrong_store=ws)
    client = TestClient(app)
    r = client.post("/api/auth/register", json={"username": "u", "password": "pw1234"})
    uid = r.json()["user"]["id"]
    h = {"Authorization": f"Bearer {r.json()['token']}"}

    qid = qs.create(uid, {"type": "single", "stem": "1+1=?", "options": ["1", "2"],
                          "answer": 1, "explanation": "", "source": "x"})
    ws.create(uid, question_id=qid, exam_id="e",
              snapshot={"type": "single", "stem": "1+1=?", "options": ["1", "2"],
                        "answer": 1, "explanation": ""}, user_answer=0)

    # 未确认：不删，回报 related_wrong=1
    resp = client.delete(f"/api/questions/{qid}", headers=h).json()
    assert resp == {"deleted": False, "related_wrong": 1}
    assert qs.get(uid, qid) is not None
    assert len(ws.list(uid)) == 1

    # 确认(force=true)：题目与对应错题一并删除
    resp2 = client.delete(f"/api/questions/{qid}?force=true", headers=h).json()
    assert resp2 == {"deleted": True, "related_wrong": 1}
    assert qs.get(uid, qid) is None
    assert ws.list(uid) == []


def test_delete_question_without_related_deletes_directly(make_mock):
    from app.questions import QuestionStore
    from app.wrong_answers import WrongAnswerStore
    qs = QuestionStore(":memory:")
    ws = WrongAnswerStore(":memory:")
    app = create_app(config=_cfg(), harness=_fake_harness(make_mock, []),
                     store=ConversationStore(":memory:"), doc_store=DocumentStore(":memory:"),
                     question_store=qs, wrong_store=ws)
    client = TestClient(app)
    r = client.post("/api/auth/register", json={"username": "u", "password": "pw1234"})
    uid = r.json()["user"]["id"]
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    qid = qs.create(uid, {"type": "short", "stem": "无错题的题", "options": None,
                          "answer": "x", "explanation": "", "source": ""})
    # 无对应错题 → 直接删除
    resp = client.delete(f"/api/questions/{qid}", headers=h).json()
    assert resp == {"deleted": True, "related_wrong": 0}
    assert qs.get(uid, qid) is None

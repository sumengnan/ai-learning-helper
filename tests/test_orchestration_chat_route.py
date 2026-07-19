"""编排器接入 /api/chat 主流程：开关开且 harness.orchestrator 存在时，主流程改为消费
Orchestrator.run() 的事件流并走现有 SSE 序列化；开关关闭时维持既有 ReAct 路径。

搭建方式照抄 tests/app/test_plan_finalize_e2e.py（直接构造 Harness + create_app + TestClient
+ c.stream 收 SSE）。
"""
import json
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


@pytest.fixture(autouse=True)
def _sqlite_allow_cross_thread(monkeypatch):
    orig = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect",
                        lambda *a, **k: orig(*a, **{**k, "check_same_thread": False}))


class FakeOrchestrator:
    """签名与真 Orchestrator.run 一致（含每请求 context/registry/recent_dialogue），只 yield 既有 Event。"""
    async def run(self, message, verify=True, *, context=None, registry=None, recent_dialogue=""):
        from harness.events import RunStarted, TextDelta, RunFinished
        from harness.types import Message, Role
        yield RunStarted(run_id="r1")
        yield TextDelta(text="编排答复")
        yield RunFinished(message=Message(role=Role.ASSISTANT, content="编排答复"))


def _client(make_mock, monkeypatch, *, orchestrator):
    """路由现在只看 harness.orchestrator 是否存在（无 enable_orchestrator 开关）。"""
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=make_mock([]), registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj),
                      system_prompt="你是助手", orchestrator=orchestrator)
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None,
                    enable_answer_gate=False)
    store = ConversationStore(":memory:")
    app = create_app(config=cfg, harness=harness, store=store,
                     doc_store=DocumentStore(":memory:"))
    return TestClient(app), store


def _auth(c):
    r = c.post("/api/auth/register", json={"username": "u", "password": "pw1234"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _chat(c, h):
    cid = c.post("/api/conversations", json={}, headers=h).json()["id"]
    with c.stream("POST", "/api/chat",
                  json={"conversation_id": cid, "message": "帮我查点资料"},
                  headers=h) as r:
        events = [json.loads(ln[6:]) for ln in r.iter_lines()
                  if ln.startswith("data: ") and ln[6:] != "[DONE]"]
    return cid, events


def _last_assistant(store, cid):
    return [m for m in store.ui_messages(cid) if m["role"] == "assistant"][-1]


class DetailOrchestrator:
    """发一条带 detail 的子代理工具进度 + 正常收尾。"""
    async def run(self, message, verify=True, *, context=None, registry=None, recent_dialogue=""):
        from harness.events import RunStarted, Progress, TextDelta, RunFinished
        from harness.types import Message, Role
        yield RunStarted(run_id="r1")
        yield Progress("subagent:executor:s1", "调用工具 calc", status="ok", key="c1",
                       detail={"tool": "calc", "args": {"x": 1}, "result": "2", "is_error": False})
        yield TextDelta(text="答复")
        yield RunFinished(message=Message(role=Role.ASSISTANT, content="答复"))


def test_orchestrator_progress_detail_streamed_and_persisted(make_mock, monkeypatch):
    """子代理工具进度的 detail 既随 SSE 下发，也随 progress 列落库（刷新后仍可展开）。"""
    c, store = _client(make_mock, monkeypatch,
                       orchestrator=DetailOrchestrator())
    cid, events = _chat(c, _auth(c))
    prog = [e for e in events if e["type"] == "Progress"
            and e["data"].get("scope") == "subagent:executor:s1"]
    assert prog and prog[-1]["data"]["detail"]["tool"] == "calc", "detail 应随 SSE 下发"
    rows = [m for m in store.ui_messages(cid) if m["role"] == "assistant"]
    saved = [p for m in rows for p in (m.get("progress") or [])
             if p.get("scope") == "subagent:executor:s1"]
    assert saved and saved[-1]["detail"]["result"] == "2", "detail 应随 progress 列落库"


def test_orchestrator_stream_becomes_main_flow(make_mock, monkeypatch):
    """开关开 + orchestrator 存在 → SSE 里流过编排器的 TextDelta，且该轮正常收尾。"""
    c, store = _client(make_mock, monkeypatch,
                       orchestrator=FakeOrchestrator())
    cid, events = _chat(c, _auth(c))

    deltas = [e for e in events if e["type"] == "TextDelta"]
    assert any("编排答复" in e["data"]["text"] for e in deltas), \
        "编排器的 TextDelta 应流过 SSE"
    assert any(e["type"] == "RunFinished" for e in events), "该轮应正常收尾"

    asst = _last_assistant(store, cid)
    assert asst["status"] == "done"
    assert asst["content"] == "编排答复", "落库的本轮 assistant 内容应为编排器的最终答复"


class EmbeddingUsageOrchestrator:
    """模拟 run 期间有 embedding/子调用经 emit 上报逐模型用量（带模型名）。"""
    async def run(self, message, verify=True, *, context=None, registry=None, recent_dialogue=""):
        from harness.events import RunStarted, TextDelta, RunFinished, ModelUsage
        from harness.usage import Usage
        from harness.progress import emit
        from harness.types import Message, Role
        yield RunStarted(run_id="r1")
        emit(ModelUsage(usage=Usage(0, 0, 42), cost_usd=0.001, attempts=1,
                        latency_ms=0.0, model="emb-model"))   # 模拟 embedding 上报
        yield TextDelta(text="答复")
        yield RunFinished(message=Message(role=Role.ASSISTANT, content="答复"))


def test_emit_model_usage_reaches_sse_and_trajectory(make_mock, monkeypatch):
    """带模型名的 emit(ModelUsage)（embedding/rerank/编排器子调用）经 _merged 并入主流 →
    既下发 SSE（前端合计）、又落 trajectory（供历史分模型统计）。"""
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=make_mock([]), registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj),
                      system_prompt="你是助手", orchestrator=EmbeddingUsageOrchestrator())
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None, enable_answer_gate=False)
    store = ConversationStore(":memory:")
    c = TestClient(create_app(config=cfg, harness=harness, store=store,
                              doc_store=DocumentStore(":memory:")))
    cid, events = _chat(c, _auth(c))
    # SSE 有该 ModelUsage（带 model 名）
    mu = [e for e in events if e["type"] == "ModelUsage" and e["data"].get("model") == "emb-model"]
    assert mu and mu[0]["data"]["usage"]["total"] == 42, "emit 的用量应下发 SSE"
    # 落进 trajectory（run_id=r1）：历史统计据此按模型分组
    traj_events = traj.load("r1")
    tmu = [e for e in traj_events if e["type"] == "ModelUsage" and e["data"].get("model") == "emb-model"]
    assert tmu and tmu[0]["data"]["usage"]["total"] == 42, "emit 的用量应落 trajectory"

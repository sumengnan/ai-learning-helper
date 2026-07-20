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
    """签名与真 Orchestrator.run 一致（含每请求 context/registry/recent_dialogue/force_simple），只 yield 既有 Event。"""
    async def run(self, message, verify=True, *, context=None, registry=None,
                  recent_dialogue="", force_simple=False, run_id=None):
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
                    enable_answer_gate=False, sandbox_approval_timeout=0.3)
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
    async def run(self, message, verify=True, *, context=None, registry=None,
                  recent_dialogue="", force_simple=False, run_id=None):
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


class RunIdToolOrchestrator:
    """尊重 run_id 参数（真 Orchestrator 已如此），并发一个 ToolStarted/ToolFinished——
    用于验证工具埋点落到 chat 登记进 conversation_runs 的 run_id 下（否则统计滤掉）。"""
    async def run(self, message, verify=True, *, context=None, registry=None,
                  recent_dialogue="", force_simple=False, run_id=None):
        from harness.events import RunStarted, ToolStarted, ToolFinished, TextDelta, RunFinished
        from harness.types import Message, Role, ToolCall, ToolResult
        yield RunStarted(run_id=run_id or "internal")
        yield ToolStarted(tool_call=ToolCall(id="t1", name="web_search", arguments={}))
        yield ToolFinished(result=ToolResult(tool_call_id="t1", content="ok", is_error=False))
        yield TextDelta(text="答复")
        yield RunFinished(message=Message(role=Role.ASSISTANT, content="答复"))


def test_orchestrator_events_recorded_under_registered_run_id(make_mock, monkeypatch):
    """编排器事件归到 chat 登记进 conversation_runs 的 run_id（修 run_id 错配）：否则
    _user_run_ids 按 conversation_runs 过滤时会把工具/步数埋点全滤掉，运行统计为空。"""
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=make_mock([]), registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj),
                      system_prompt="你是助手", orchestrator=RunIdToolOrchestrator())
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None, enable_answer_gate=False)
    store = ConversationStore(":memory:")
    c = TestClient(create_app(config=cfg, harness=harness, store=store,
                              doc_store=DocumentStore(":memory:")))
    cid, _ = _chat(c, _auth(c))
    # conversation_runs 里登记的 run_id 下应能查到 ToolStarted（证明事件归到了对的 id）。
    # run_ids 带归属校验，需用会话真实 owner 的 user_id（非登录名字面量）。
    uid = store._conn.execute("SELECT user_id FROM conversations WHERE id=?", (cid,)).fetchone()[0]
    run_ids = store.run_ids(uid, cid)
    recorded = [e for rid in run_ids for e in traj.load(rid)]
    tools = [e for e in recorded
             if e["type"] == "ToolStarted" and e["data"]["tool_call"]["name"] == "web_search"]
    assert tools, "编排器的 ToolStarted 应落在 conversation_runs 登记的 run_id 下，统计才认得"


class EmbeddingUsageOrchestrator:
    """模拟 run 期间有 embedding/子调用经 emit 上报逐模型用量（带模型名）。"""
    async def run(self, message, verify=True, *, context=None, registry=None,
                  recent_dialogue="", force_simple=False, run_id=None):
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


# ---------- 交付门开门信号：生成的文件在校验完成前不得显示 ----------

class FileToolOrchestrator:
    """模拟执行子步调 save_download：ToolFinished 里带〔下载ID:x〕机读标记。"""
    async def run(self, message, verify=True, *, context=None, registry=None,
                  recent_dialogue="", force_simple=False, run_id=None):
        from harness.events import RunStarted, ToolStarted, ToolFinished, RunFinished
        from harness.types import Message, Role, ToolCall, ToolResult
        yield RunStarted(run_id=run_id or "r1")
        yield ToolStarted(tool_call=ToolCall(id="c1", name="save_download",
                                             arguments={"filename": "报告.md"}))
        yield ToolFinished(result=ToolResult(
            "c1", "已保存到下载区：报告.md（12 字节）。〔下载ID:dl1〕", is_error=False))
        yield RunFinished(message=Message(role=Role.ASSISTANT, content="给你报告"))


def _chat_verify(c, h, verify):
    cid = c.post("/api/conversations", json={}, headers=h).json()["id"]
    with c.stream("POST", "/api/chat",
                  json={"conversation_id": cid, "message": "写份报告", "verify": verify},
                  headers=h) as r:
        return [json.loads(ln[6:]) for ln in r.iter_lines()
                if ln.startswith("data: ") and ln[6:] != "[DONE]"]


def _kinds(events):
    return [e["type"] if e["type"] != "Progress" else f"Progress:{e['data']['scope']}"
            for e in events]


def test_gate_open_precedes_any_tool_event(make_mock, monkeypatch):
    """回归：开门信号原先只在 ReAct+交付门分支里发，而编排器已是唯一主流程，于是整套
    「交付前盖住生成的文件」形同虚设——文件在校验开始前就显示出来了。

    信号必须早于**任何**工具事件：save_download 远早于编排器那条「结果校验中…」
    （后者要等所有步骤跑完），只断言「发过了」不足以保证不闪一下。"""
    from app.api.chat import GATE_OPEN_KEY
    c, _ = _client(make_mock, monkeypatch, orchestrator=FileToolOrchestrator())
    events = _chat_verify(c, _auth(c), True)
    kinds = _kinds(events)

    assert "Progress:verify" in kinds, "编排器路径应下发开门信号"
    first_verify = kinds.index("Progress:verify")
    for tool_ev in ("ToolStarted", "ToolFinished"):
        assert tool_ev in kinds and first_verify < kinds.index(tool_ev), \
            f"开门信号必须早于 {tool_ev}，否则文件会先显示出来"

    sig = next(e for e in events
               if e["type"] == "Progress" and e["data"]["scope"] == "verify")
    assert sig["data"]["key"] == GATE_OPEN_KEY      # 前端靠这个 key 认出它
    assert sig["data"]["status"] == "running"


def test_no_gate_open_when_verify_off(make_mock, monkeypatch):
    """关校验时不得下发：编排器此时一条 verify 事件都不发，前端见不到信号即照常显示。
    若这里误发，文件会被盖住直到流结束——比提前显示更糟（可能永远不显示）。"""
    c, _ = _client(make_mock, monkeypatch, orchestrator=FileToolOrchestrator())
    events = _chat_verify(c, _auth(c), False)
    assert "Progress:verify" not in _kinds(events)


# ---------- 危险命令人工审核：整条链路必须在编排器路径上通 ----------

class ApprovalOrchestrator:
    """模拟执行子步里工具命中危险命令时的行为：run_shell 检出后即调 request_approval。"""
    async def run(self, message, verify=True, *, context=None, registry=None,
                  recent_dialogue="", force_simple=False, run_id=None):
        from harness.events import RunStarted, RunFinished
        from harness.types import Message, Role
        from harness.approval import request_approval
        yield RunStarted(run_id=run_id or "r1")
        ok = await request_approval("run_shell", "rm -rf /workspace", "递归/强制删除文件")
        yield RunFinished(message=Message(role=Role.ASSISTANT, content=f"approved={ok}"))


def test_approval_required_reaches_sse_on_orchestrator_path(make_mock, monkeypatch):
    """此前只有 approval 的单元测试与工具级测试，没有一条覆盖聊天路径——而
    request_approval 在「拿不到审批上下文」时是**静默放行**的（approval.py: ctx is None
    → return True）。上下文由 chat.py 的 pump() 设置；若编排器路径漏设，危险命令会被无声
    执行、用户永远看不到弹窗。这条测试就是钉住这一点。"""
    c, _ = _client(make_mock, monkeypatch, orchestrator=ApprovalOrchestrator())
    h = _auth(c)
    cid = c.post("/api/conversations", json={}, headers=h).json()["id"]
    with c.stream("POST", "/api/chat",
                  json={"conversation_id": cid, "message": "删掉工作区"}, headers=h) as r:
        events = [json.loads(ln[6:]) for ln in r.iter_lines()
                  if ln.startswith("data: ") and ln[6:] != "[DONE]"]

    kinds = [e["type"] for e in events]
    assert "ApprovalRequired" in kinds, "危险命令必须下发审批事件，否则前端无从弹窗"
    req = next(e for e in events if e["type"] == "ApprovalRequired")
    assert req["data"]["command"] == "rm -rf /workspace"
    assert req["data"]["reason"] and req["data"]["approval_id"]
    # 无人应答 → 超时自动拒绝，而不是放行
    fin = next(e for e in events if e["type"] == "RunFinished")
    assert fin["data"]["message"]["content"] == "approved=False"

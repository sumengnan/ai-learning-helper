"""交付门端到端：缓冲 → 校验 → 重答/降级。TestClient + MockModelClient + 注入桩校验器。"""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.assembly import Harness
from app.main import create_app
from app.conversations import ConversationStore
from app.documents import DocumentStore
from app.verify import Verdict
from harness.persistence.checkpoint import CheckpointStore
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink
from harness.tools.base import ToolRegistry
from harness.tools.builtins.calculator import CalculatorTool


@pytest.fixture(autouse=True)
def _sqlite_allow_cross_thread(monkeypatch):
    orig = sqlite3.connect

    def _patched(*a, **k):
        k["check_same_thread"] = False
        return orig(*a, **k)
    monkeypatch.setattr(sqlite3, "connect", _patched)


class _StubVerifier:
    """按序返回预设 Verdict；记录调用次数与看到的答案。"""
    def __init__(self, verdicts):
        self._v = list(verdicts)
        self.calls = 0
        self.seen: list[str] = []

    async def verify(self, question, answer, grounding, registry, steps=None):
        self.seen.append(answer)
        v = self._v[min(self.calls, len(self._v) - 1)]
        self.calls += 1
        return v


def _harness(make_mock, turns):
    reg = ToolRegistry(); reg.register(CalculatorTool())
    traj = TrajectoryStore(":memory:")
    return Harness(client=make_mock(turns), registry=reg,
                   checkpoint_store=CheckpointStore(":memory:"),
                   trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="你是助手")


def _client(make_mock, turns, verifier, **cfg_kw):
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None,
                    enable_answer_gate=True, **cfg_kw)
    store = ConversationStore(":memory:")
    app = create_app(config=cfg, harness=_harness(make_mock, turns), store=store,
                     doc_store=DocumentStore(":memory:"), verifier=verifier)
    return TestClient(app), store


def _auth(client):
    r = client.post("/api/auth/register", json={"username": "u", "password": "pw1234"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _events(resp):
    out = []
    for line in resp.iter_lines():
        if line and line.startswith("data: "):
            out.append(json.loads(line[6:]))
    return out


def _run_chat(client, h, message):
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": message}, headers=h) as resp:
        assert resp.status_code == 200
        return cid, _events(resp)


def _final(events):
    rf = [e for e in events if e["type"] == "RunFinished"]
    return rf[-1]["data"]["message"]["content"] if rf else None


def _verify_progress(events):
    return [e["data"]["text"] for e in events
            if e["type"] == "Progress" and e["data"]["scope"] == "verify"]


def test_fail_then_reanswer_delivers_second_version(make_mock, text_turn):
    verifier = _StubVerifier([
        Verdict(ok=False, failed=["judge"], critique="太笼统", summary="judge"),
        Verdict(ok=True),
    ])
    client, store = _client(make_mock, [text_turn("初版答案"), text_turn("修正版答案")],
                            verifier, answer_gate_max_retries=1)
    h = _auth(client)
    cid, events = _run_chat(client, h, "问个问题")

    assert verifier.calls == 2                          # 首答不过 → 重答一次
    vp = _verify_progress(events)
    assert any("校验中" in t for t in vp) and any("重答中" in t for t in vp)
    assert _final(events) == "修正版答案"                # 交付的是修正版
    # 草稿未逐字流给用户：TextDelta 只应是交付阶段补发的终稿分片，拼起来=修正版
    deltas = "".join(e["data"]["text"] for e in events if e["type"] == "TextDelta")
    assert deltas == "修正版答案"
    msgs = [m.content for m in store.messages(cid)]
    assert msgs == ["问个问题", "修正版答案"]            # 落库为交付版


def test_exhausted_retries_degrades_with_warning(make_mock, text_turn):
    verifier = _StubVerifier([Verdict(ok=False, failed=["grounding"],
                                      critique="缺依据", summary="grounding")])
    client, store = _client(make_mock, [text_turn("可疑答案")],
                            verifier, answer_gate_max_retries=0)   # 总尝试 1 次
    h = _auth(client)
    cid, events = _run_chat(client, h, "问")

    assert verifier.calls == 1
    final = _final(events)
    assert final.startswith("⚠️") and "可疑答案" in final and "grounding" in final
    assert store.messages(cid)[-1].content.startswith("⚠️")     # 落库含告示


def test_first_answer_passes_delivers_as_is(make_mock, text_turn):
    verifier = _StubVerifier([Verdict(ok=True)])
    client, store = _client(make_mock, [text_turn("好答案")], verifier)
    h = _auth(client)
    cid, events = _run_chat(client, h, "问")

    assert verifier.calls == 1
    assert _final(events) == "好答案"
    assert any("校验通过" in t for t in _verify_progress(events))
    assert store.messages(cid)[-1].content == "好答案"


def test_gate_off_passthrough_streams_live(make_mock, text_turn):
    # enable_answer_gate=False（且不注入 verifier）→ 走直通流式，不校验
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None,
                    enable_answer_gate=False)
    store = ConversationStore(":memory:")
    app = create_app(config=cfg, harness=_harness(make_mock, [text_turn("直通答案")]),
                     store=store, doc_store=DocumentStore(":memory:"))
    client = TestClient(app)
    h = _auth(client)
    cid, events = _run_chat(client, h, "问")
    assert _verify_progress(events) == []               # 无校验进度
    assert _final(events) == "直通答案"
    assert store.messages(cid)[-1].content == "直通答案"


def test_gate_off_empty_completion_surfaces_error(make_mock):
    """直通模式下模型空产出（无文本/无工具调用/无异常）：必须给在途客户端补发可见 RunError，
    并落库为 error——否则前端会静默显示"…"+"已完成"，把失败伪装成成功。"""
    from harness.llm.base import StreamChunk
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None,
                    enable_answer_gate=False)
    store = ConversationStore(":memory:")
    empty_turn = [StreamChunk(type="done")]             # 只有 done：既无 text 也无 tool_call
    app = create_app(config=cfg, harness=_harness(make_mock, [empty_turn]),
                     store=store, doc_store=DocumentStore(":memory:"))
    client = TestClient(app)
    h = _auth(client)
    cid, events = _run_chat(client, h, "问")
    assert any(e["type"] == "RunError" for e in events), "空产出应补发 RunError 让前端标红"
    assert store.ui_messages(cid)[-1]["status"] == "error"   # 落库为 error 而非 done


def test_gate_off_empty_completion_persists_error_text(make_mock):
    """直通空产出：落库内容应带上真实错误文案（与在途 RunError 一致），刷新后仍能看到
    「为何失败」，而不是退化成泛化的「本轮未完成」（刷新后消息内容错误的根因回归测试）。"""
    from harness.llm.base import StreamChunk
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None,
                    enable_answer_gate=False)
    store = ConversationStore(":memory:")
    empty_turn = [StreamChunk(type="done")]
    app = create_app(config=cfg, harness=_harness(make_mock, [empty_turn]),
                     store=store, doc_store=DocumentStore(":memory:"))
    client = TestClient(app)
    h = _auth(client)
    cid, events = _run_chat(client, h, "问")
    err = next(e["data"]["error"] for e in events if e["type"] == "RunError")
    last = store.ui_messages(cid)[-1]
    assert last["status"] == "error"
    assert last["content"] != "（本轮未完成）"        # 不应退化成泛化占位
    assert err in last["content"]                     # 落库内容 == 在途所见错误文案

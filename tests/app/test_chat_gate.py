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


def test_side_effect_ids_groups_by_tool():
    from app.api.chat import _side_effect_ids
    steps = [
        {"tool": "save_download", "result": "已保存〔下载ID:aaa〕", "is_error": False},
        {"tool": "save_to_knowledge", "result": "已存〔知识ID:kkk〕", "is_error": False},
        {"tool": "add_questions", "result": "已入库〔题目ID:q1,q2〕", "is_error": False},
        {"tool": "save_download", "result": "保存失败", "is_error": True},          # 失败步不算
    ]
    fx = _side_effect_ids(steps)
    assert fx["download"] == ["aaa"]
    assert fx["knowledge"] == ["kkk"]
    assert fx["questions"] == ["q1", "q2"]


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


def _verify_trace(store, cid):
    """落库的交付门结构化判定（conversation_messages.verify 列）。"""
    msgs = store.ui_messages(cid)
    return [m["verify"] for m in msgs if m["role"] == "assistant"][-1]


def test_verify_trace_persists_retries_and_failed_layers(make_mock, text_turn):
    # 重答一次后通过 → 落库须能回答「重答几次、哪层没过、被否的那次是哪个 run」
    verifier = _StubVerifier([
        Verdict(ok=False, failed=["judge", "grounding"], critique="太笼统",
                summary="judge、grounding", hard_failed=["grounding"]),
        Verdict(ok=True),
    ])
    client, store = _client(make_mock, [text_turn("初版"), text_turn("修正版")],
                            verifier, answer_gate_max_retries=1)
    h = _auth(client)
    cid, _ = _run_chat(client, h, "问个问题")

    vt = _verify_trace(store, cid)
    assert vt["attempts"] == 2 and vt["retries"] == 1
    assert vt["ok"] is True and vt["degraded"] is False
    assert len(vt["history"]) == 2
    first = vt["history"][0]
    assert first["attempt"] == 1 and first["ok"] is False
    # 结构化保留：progress 列那份中文文案拍扁后统计不出这些
    assert first["failed"] == ["judge", "grounding"]
    assert first["hard_failed"] == ["grounding"]
    assert first["critique"] == "太笼统"
    assert vt["history"][1]["ok"] is True
    # run_id 是与 harness 库 trajectory_events 的接缝，两次尝试各不相同
    assert first["run_id"] != vt["history"][1]["run_id"]


def test_verify_trace_run_id_links_to_rejected_draft(make_mock, text_turn):
    # run_id 得真能捞回被否草稿的原文，否则这个字段没意义
    verifier = _StubVerifier([
        Verdict(ok=False, failed=["judge"], critique="不行", summary="judge"),
        Verdict(ok=True),
    ])
    reg = ToolRegistry(); reg.register(CalculatorTool())
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=make_mock([text_turn("初版草稿"), text_turn("修正版")]),
                      registry=reg, checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="你是助手")
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None,
                    enable_answer_gate=True, answer_gate_max_retries=1)
    store = ConversationStore(":memory:")
    app = create_app(config=cfg, harness=harness, store=store,
                     doc_store=DocumentStore(":memory:"), verifier=verifier)
    client = TestClient(app)
    h = _auth(client)
    cid, _ = _run_chat(client, h, "问个问题")

    rejected_run = _verify_trace(store, cid)["history"][0]["run_id"]
    evs = traj.load(rejected_run)
    text = "".join(e["data"].get("text", "") for e in evs if e["type"] == "TextDelta")
    assert "初版草稿" in text          # 据 run_id 捞回了那次被否的原文


def test_verify_trace_single_pass_has_no_retries(make_mock, text_turn):
    verifier = _StubVerifier([Verdict(ok=True)])
    client, store = _client(make_mock, [text_turn("好答案")], verifier)
    h = _auth(client)
    cid, _ = _run_chat(client, h, "问个问题")

    vt = _verify_trace(store, cid)
    assert vt["attempts"] == 1 and vt["retries"] == 0 and vt["ok"] is True
    assert vt["degraded"] is False and len(vt["history"]) == 1


def test_verify_trace_marks_degraded_when_retries_exhausted(make_mock, text_turn):
    verifier = _StubVerifier([Verdict(ok=False, failed=["judge"], critique="差",
                                      summary="judge")])
    client, store = _client(make_mock, [text_turn("烂答案"), text_turn("还是烂")],
                            verifier, answer_gate_max_retries=1)
    h = _auth(client)
    cid, _ = _run_chat(client, h, "问个问题")

    vt = _verify_trace(store, cid)
    assert vt["attempts"] == 2 and vt["retries"] == 1
    assert vt["ok"] is False and vt["degraded"] is True     # 用尽次数仍不过 → 降级交付
    assert all(h_["ok"] is False for h_ in vt["history"])


def test_verify_trace_absent_when_gate_off(make_mock, text_turn):
    # 门没开就没有判定可记，不该写入噪音
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None,
                    enable_answer_gate=False)
    store = ConversationStore(":memory:")
    app = create_app(config=cfg, harness=_harness(make_mock, [text_turn("直通答案")]),
                     store=store, doc_store=DocumentStore(":memory:"))
    client = TestClient(app)
    h = _auth(client)
    cid, _ = _run_chat(client, h, "问个问题")
    assert _verify_trace(store, cid) is None


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


def test_verify_toggle_off_skips_gate(make_mock, text_turn):
    # 本轮 verify=False：即便装配了 verifier 且服务端开关开，也跳过校验、直通产出
    verifier = _StubVerifier([Verdict(ok=True)])
    client, store = _client(make_mock, [text_turn("直接产出")], verifier)
    h = _auth(client)
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": "问", "verify": False},
                       headers=h) as resp:
        assert resp.status_code == 200
        events = _events(resp)
    assert verifier.calls == 0                      # 关校验 → verifier 未被调用
    assert _final(events) == "直接产出"
    assert not _verify_progress(events)             # 无校验进度


def test_verify_toggle_on_default_runs_gate(make_mock, text_turn):
    # 不传 verify（默认 True）→ 校验照常运行
    verifier = _StubVerifier([Verdict(ok=True)])
    client, store = _client(make_mock, [text_turn("好答案")], verifier)
    h = _auth(client)
    _cid, events = _run_chat(client, h, "问")
    assert verifier.calls == 1 and _final(events) == "好答案"


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


# ---- 校验器自身故障：fail-open 交付，但必须留痕 ----

class _BoomVerifier:
    def __init__(self):
        self.calls = 0

    async def verify(self, question, answer, grounding, registry, steps=None):
        self.calls += 1
        raise RuntimeError("verifier 内部炸了")


def test_verifier_crash_delivers_draft_instead_of_losing_it(make_mock, text_turn, caplog):
    # 交付门是质量增强，它坏了不该连累用户丢掉一份好答案（此前是 fail-closed：整轮 RunError）
    verifier = _BoomVerifier()
    client, store = _client(make_mock, [text_turn("一份好答案")], verifier)
    h = _auth(client)
    with caplog.at_level("WARNING"):
        cid, events = _run_chat(client, h, "问个问题")

    assert verifier.calls == 1
    assert _final(events) == "一份好答案"                     # 答案照常交付，没被丢
    assert not [e for e in events if e["type"] == "RunError"]
    msg = [m for m in store.ui_messages(cid) if m["role"] == "assistant"][-1]
    assert msg["status"] == "done"
    # 但绝不能无声无息：日志 + verify 列都要留痕
    assert any("校验器故障" in r.message for r in caplog.records)
    assert "verifier 内部炸了" in msg["verify"]["gate_error"]


def test_verifier_crash_does_not_retry(make_mock, text_turn):
    # 基建故障重答也没用，只会白烧一轮 token
    verifier = _BoomVerifier()
    client, _ = _client(make_mock, [text_turn("答案")], verifier, answer_gate_max_retries=2)
    h = _auth(client)
    _run_chat(client, h, "问个问题")
    assert verifier.calls == 1


def test_healthy_verifier_has_no_gate_error(make_mock, text_turn):
    client, store = _client(make_mock, [text_turn("好答案")], _StubVerifier([Verdict(ok=True)]))
    h = _auth(client)
    cid, _ = _run_chat(client, h, "问个问题")
    vt = [m for m in store.ui_messages(cid) if m["role"] == "assistant"][-1]["verify"]
    assert vt["gate_error"] is None      # 正常路径不该冒出噪音

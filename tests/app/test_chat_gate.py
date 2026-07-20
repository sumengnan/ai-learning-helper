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
from app.api.chat import GATE_OPEN_KEY
from harness.persistence.checkpoint import CheckpointStore
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink
from harness.tools.base import Tool, ToolRegistry
from harness.tools.builtins.calculator import CalculatorTool
from pydantic import BaseModel


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


def test_side_effect_ids_tracks_generate_questions_too():
    """generate_questions 同样往题库写题，失败轮须一并清理。"""
    from app.api.chat import _side_effect_ids
    steps = [
        {"tool": "generate_questions", "result": "已生成〔题目ID:g1,g2〕", "is_error": False},
        {"tool": "add_questions", "result": "已入库〔题目ID:a1〕", "is_error": False},
        {"tool": "generate_questions", "result": "出题失败", "is_error": True},
    ]
    assert _side_effect_ids(steps)["questions"] == ["g1", "g2", "a1"]


class _StubVerifier:
    """按序返回预设 Verdict；记录调用次数、看到的答案与 recent_dialogue。"""
    def __init__(self, verdicts):
        self._v = list(verdicts)
        self.calls = 0
        self.seen: list[str] = []
        self.prev_replies: list[str] = []

    async def verify(self, question, answer, grounding, registry, steps=None, recent_dialogue=""):
        self.seen.append(answer)
        self.prev_replies.append(recent_dialogue)
        v = self._v[min(self.calls, len(self._v) - 1)]
        self.calls += 1
        return v


def test_gate_passes_prev_assistant_turn_to_judge(make_mock, text_turn):
    """端到端接线：上一轮 AI 给了菜单、用户回「A」，交付门要把上一轮 AI 的话作为
    recent_dialogue 传给校验器——否则 judge 会把「A」误判为含义不明。"""
    verifier = _StubVerifier([Verdict(ok=True), Verdict(ok=True)])
    client, store = _client(make_mock,
                            [text_turn("请回复 A / B / C 选择方案"), text_turn("按方案 A 执行完毕")],
                            verifier)
    h = _auth(client)
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    _run_chat_on(client, h, cid, "帮我出个方案")     # 第一轮：AI 摆出菜单
    _run_chat_on(client, h, cid, "A")                # 第二轮：用户选 A

    # 第二次校验拿到的 recent_dialogue 应是上一轮 AI 的菜单
    assert "A / B / C" in verifier.prev_replies[-1]
    # 第一轮无上一轮 AI 话 → recent_dialogue 为空，不硬塞
    assert verifier.prev_replies[0] == ""


def _run_chat_on(client, h, cid, message):
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": message}, headers=h) as resp:
        assert resp.status_code == 200
        return _events(resp)


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
    # 两版都逐字流给了用户：初版先流式显示 → 未过发 reset 清屏 → 修正版再流式
    kinds = [(e["type"], e["data"].get("scope") if e["type"] == "Progress" else None)
             for e in events]
    assert ("Progress", "reset") in kinds               # 重答前清屏信号
    deltas = "".join(e["data"]["text"] for e in events if e["type"] == "TextDelta")
    assert "初版答案" in deltas and "修正版答案" in deltas  # 初版确实显示过，不是被缓冲吞掉
    # reset 之后的 TextDelta 拼起来 = 最终交付版（清屏后重新打字机输出）
    ridx = next(i for i, e in enumerate(events)
                if e["type"] == "Progress" and e["data"].get("scope") == "reset")
    after = "".join(e["data"]["text"] for e in events[ridx:] if e["type"] == "TextDelta")
    assert after == "修正版答案"
    msgs = [m.content for m in store.messages(cid)]
    assert msgs == ["问个问题", "修正版答案"]            # 落库为交付版（不含被清屏的初版）


def test_exhausted_retries_degrades_keeps_shown_answer(make_mock, text_turn):
    """降级交付：最后一版已流式显示给用户，保留原样、不在正文前拼 ⚠️ 告示。
    未过由红色「结果校验未通过」徽章 + verify_trace.degraded 表达，正文不被篡改。"""
    verifier = _StubVerifier([Verdict(ok=False, failed=["grounding"],
                                      critique="缺依据", summary="grounding")])
    client, store = _client(make_mock, [text_turn("可疑答案")],
                            verifier, answer_gate_max_retries=0)   # 总尝试 1 次
    h = _auth(client)
    cid, events = _run_chat(client, h, "问")

    assert verifier.calls == 1
    assert _final(events) == "可疑答案"                         # 正文原样，无 ⚠️ 前缀
    assert store.messages(cid)[-1].content == "可疑答案"        # 落库亦原样（与屏上一致）
    assert "⚠️" not in _final(events)
    vt = _verify_trace(store, cid)
    assert vt["degraded"] is True                              # 降级状态仍记录
    assert any(e["type"] == "Progress" and e["data"]["scope"] == "verify"
               and e["data"]["status"] == "error" for e in events)   # 红色未通过事件在


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

    async def verify(self, question, answer, grounding, registry, steps=None, recent_dialogue=""):
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


# ---------- 未交付的产物不该提前露出 / 重答后要清掉 ----------

def test_gate_signals_before_agent_runs(make_mock, text_turn):
    """校验门开启时，首个 verify 信号须早于工具执行。

    前端据此在交付前一直盖住「生成的文件」——工具执行先于「校验中…」，若等到那时才发信号，
    文件按钮会先冒出来再消失，闪一下。
    """
    client, _ = _client(make_mock, [text_turn("答案")], _StubVerifier([Verdict(ok=True)]))
    h = _auth(client)
    _cid, events = _run_chat(client, h, "问")
    kinds = [e["type"] if e["type"] != "Progress" else f"Progress:{e['data']['scope']}"
             for e in events]
    assert "Progress:verify" in kinds
    # 首个 verify 信号必须是 running（在途标记），且排在所有正文/终态事件之前
    first_verify = kinds.index("Progress:verify")
    for term in ("RunFinished", "TextDelta"):
        if term in kinds:
            assert first_verify < kinds.index(term), f"verify 信号应早于 {term}"
    assert _verify_progress(events)[0] == "生成中…"
    # 它得带固定的 GATE_OPEN_KEY：前端靠这个 key 把它认出来并排除在校验徽章之外
    # （它先于任何校验发生，起徽章就等于谎称在校验），同时仍据它盖住生成的文件。
    first = next(e for e in events
                 if e["type"] == "Progress" and e["data"]["scope"] == "verify")
    assert first["data"]["key"] == GATE_OPEN_KEY


def test_gate_running_text_names_its_layer(make_mock, text_turn):
    """交付门的「进行中」文案必须自报是结果校验。

    徽章原样显示这条文案，且只有交付门会发 running（每步校验在工具跑完时直接出 ok/error，
    没有进行中态）。终态行一直都写明层级（「结果校验通过」/「步骤校验未通过」），若进行中
    只说「校验中…」，用户就看不出转圈的是交付门还是每步校验——两套机制彼此独立、可各自开关。
    """
    client, _ = _client(make_mock, [text_turn("答案")], _StubVerifier([Verdict(ok=True)]))
    h = _auth(client)
    _cid, events = _run_chat(client, h, "问")
    running = [e["data"]["text"] for e in events
               if e["type"] == "Progress" and e["data"]["scope"] == "verify"
               and e["data"]["status"] == "running" and e["data"]["key"] != GATE_OPEN_KEY]
    assert running and running[0] == "结果校验中…"


class _FakeDownloadStore:
    """内存版下载库：记录建/删。用真的 SaveDownloadTool 打它 —— create_app 会用
    harness.download_store 重建该工具，注入桩工具反而会被覆盖掉。"""
    def __init__(self):
        self.created: list[str] = []
        self.deleted: list[str] = []
        self._n = 0

    def create(self, user_id, filename, data, content_type):
        self._n += 1
        did = f"dl{self._n}"
        self.created.append(did)
        return {"id": did, "filename": filename, "size": len(data),
                "content_type": content_type}

    def delete(self, user_id, did):
        self.deleted.append(did)
        return True


def _client_with_downloads(make_mock, turns, verifier, dl_store, **cfg_kw):
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None,
                    enable_answer_gate=True, **cfg_kw)
    hn = _harness(make_mock, turns)
    hn.download_store = dl_store
    store = ConversationStore(":memory:")
    app = create_app(config=cfg, harness=hn, store=store,
                     doc_store=DocumentStore(":memory:"), verifier=verifier)
    return TestClient(app), store


def _dl_turns(tool_turn, text_turn):
    """两版：各生成一个文件 + 一段正文。"""
    return [tool_turn("save_download", '{"filename":"a.md","content":"x"}', call_id="c1"),
            text_turn("第一版"),
            tool_turn("save_download", '{"filename":"b.md","content":"y"}', call_id="c2"),
            text_turn("第二版")]


def test_retry_purges_previous_download(make_mock, tool_turn, text_turn):
    """重答后，被否那版生成的文件必须删掉、交付那版的必须留下。

    否则用户的下载列表里会多出一个对应着「已作废回答」的文件。
    """
    dl = _FakeDownloadStore()
    verifier = _StubVerifier([Verdict(ok=False, failed=["judge"], critique="不行", summary="judge"),
                              Verdict(ok=True)])
    client, _ = _client_with_downloads(make_mock, _dl_turns(tool_turn, text_turn), verifier, dl,
                                       answer_gate_max_retries=1)
    h = _auth(client)
    _cid, events = _run_chat(client, h, "生成个文件")
    assert _final(events) == "第二版"
    assert dl.created == ["dl1", "dl2"]          # 两版各建了一个
    assert dl.deleted == ["dl1"], "被否那版的文件该删"


def test_pass_first_try_keeps_download(make_mock, tool_turn, text_turn):
    """一次就过 → 不该误删本轮产物。"""
    dl = _FakeDownloadStore()
    turns = [tool_turn("save_download", '{"filename":"a.md","content":"x"}', call_id="c1"),
             text_turn("答案")]
    client, _ = _client_with_downloads(make_mock, turns, _StubVerifier([Verdict(ok=True)]), dl)
    h = _auth(client)
    _cid, events = _run_chat(client, h, "生成个文件")
    assert _final(events) == "答案"
    assert dl.created == ["dl1"] and dl.deleted == []


def test_degraded_delivery_keeps_last_download(make_mock, tool_turn, text_turn):
    """用尽重答次数 → 降级交付最后一版：那版的文件要留给用户，之前被否的要删。"""
    dl = _FakeDownloadStore()
    verifier = _StubVerifier([Verdict(ok=False, failed=["judge"], critique="不行", summary="judge")])
    client, _ = _client_with_downloads(make_mock, _dl_turns(tool_turn, text_turn), verifier, dl,
                                       answer_gate_max_retries=1)
    h = _auth(client)
    _cid, _events = _run_chat(client, h, "生成个文件")
    assert dl.deleted == ["dl1"], "只删被否那版；降级交付的那版要留"


def _ui_steps(store, cid):
    msgs = store.ui_messages(cid)
    return [m["steps"] for m in msgs if m["role"] == "assistant"][-1] or []


def test_retry_drops_purged_download_mark_from_steps(make_mock, tool_turn, text_turn):
    """被否那版的文件删了，落库步骤里的〔下载ID:x〕标记也必须抹掉。

    前端据这个标记渲染下载按钮、且 steps 累积了本轮所有尝试；留着标记 → 重答后并排出现
    新旧两个按钮，点旧的必然 404（对应文件已被 _purge_side_effects 删掉）。
    """
    dl = _FakeDownloadStore()
    verifier = _StubVerifier([Verdict(ok=False, failed=["judge"], critique="不行", summary="judge"),
                              Verdict(ok=True)])
    client, store = _client_with_downloads(make_mock, _dl_turns(tool_turn, text_turn), verifier, dl,
                                           answer_gate_max_retries=1)
    h = _auth(client)
    cid, _ = _run_chat(client, h, "生成个文件")
    assert dl.deleted == ["dl1"]

    marks = [s.get("result") or "" for s in _ui_steps(store, cid)]
    joined = "\n".join(marks)
    assert "〔下载ID:dl1〕" not in joined, "被删文件的标记必须抹掉，否则前端渲染出死按钮"
    assert "〔下载ID:dl2〕" in joined, "交付那版的标记要留着，用户得能下载"
    assert "已作废" in joined, "抹掉标记的同时要说明原因，别让轨迹变得莫名其妙"


def _purged_events(events):
    return [json.loads(e["data"]["text"]) for e in events
            if e["type"] == "Progress" and e["data"]["scope"] == "purged"]


def test_retry_tells_client_which_downloads_were_purged(make_mock, tool_turn, text_turn):
    """服务端得把「删了哪些下载」推给在途客户端。

    前端在途的 steps 是从 ToolFinished 攒的、早拿到了〔下载ID:x〕；只改落库那份的话，
    在途界面会留一个指向已删文件的死按钮（要刷新才好）—— 而在途正是用户主要看的路径。
    """
    dl = _FakeDownloadStore()
    verifier = _StubVerifier([Verdict(ok=False, failed=["judge"], critique="不行", summary="judge"),
                              Verdict(ok=True)])
    client, _ = _client_with_downloads(make_mock, _dl_turns(tool_turn, text_turn), verifier, dl,
                                       answer_gate_max_retries=1)
    h = _auth(client)
    _cid, events = _run_chat(client, h, "生成个文件")
    assert _purged_events(events) == [["dl1"]], "被删的下载 id 要推给在途客户端"


def test_no_purge_event_when_nothing_purged(make_mock, tool_turn, text_turn):
    """一次就过 → 没东西可删 → 不发噪音事件。"""
    dl = _FakeDownloadStore()
    turns = [tool_turn("save_download", '{"filename":"a.md","content":"x"}', call_id="c1"),
             text_turn("答案")]
    client, _ = _client_with_downloads(make_mock, turns, _StubVerifier([Verdict(ok=True)]), dl)
    h = _auth(client)
    _cid, events = _run_chat(client, h, "生成个文件")
    assert _purged_events(events) == []
# ---- 交付门内部跑的代码不该出现在用户可见的「参考来源」里 ----

class _CodeRunningVerifier:
    """模拟 gate_check_code：从传入的 registry 取 run_python 跑答案里的代码块。"""
    def __init__(self):
        self.ran = False

    async def verify(self, question, answer, grounding, registry, steps=None, recent_dialogue=""):
        tool = registry.get("run_python")
        if tool is not None:
            await tool.run(tool.Params(code="print(6*7)"))
            self.ran = True
        return Verdict(ok=True)


def _harness_with_py(make_mock, turns):
    from pydantic import BaseModel
    from harness.tools.base import Tool

    class _Py(Tool):
        name = "run_python"
        description = "跑 python"

        class Params(BaseModel):
            code: str

        async def run(self, params):
            return "stdout:\n42\n(exit 0)"

    reg = ToolRegistry(); reg.register(_Py())
    traj = TrajectoryStore(":memory:")
    return Harness(client=make_mock(turns), registry=reg,
                   checkpoint_store=CheckpointStore(":memory:"),
                   trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="你是助手")


def test_gate_code_execution_not_credited_as_source(make_mock, text_turn):
    verifier = _CodeRunningVerifier()
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None,
                    enable_answer_gate=True)
    store = ConversationStore(":memory:")
    app = create_app(config=cfg, harness=_harness_with_py(make_mock, [text_turn("答案正文")]),
                     store=store, doc_store=DocumentStore(":memory:"), verifier=verifier)
    client = TestClient(app)
    h = _auth(client)
    cid, events = _run_chat(client, h, "问个问题")

    assert verifier.ran is True                       # 交付门确实跑了代码
    msg = [m for m in store.ui_messages(cid) if m["role"] == "assistant"][-1]
    # 用户从没看见 AI 执行 python，来源里也不该冒出来
    assert not (msg["sources"] or []), f"交付门内部调用泄漏进了参考来源：{msg['sources']}"
    src_events = [e for e in events if e["type"] == "Progress"
                  and e["data"].get("scope") == "sources"]
    assert not src_events                             # SSE 也不该推


# ---- 重答时副作用产物的重做提示 ----

def test_redo_fx_note_names_the_artifacts():
    from app.api.chat import _redo_fx_note
    note = _redo_fx_note([
        {"tool": "save_download", "args": {"filename": "7天AI学习计划.md"},
         "result": "已保存〔下载ID:d1〕", "is_error": False},
        {"tool": "calculator", "args": {}, "result": "42", "is_error": False},
    ])
    assert "save_download" in note and "7天AI学习计划.md" in note
    assert "必须重新调用相应工具再存一次" in note
    assert "calculator" not in note          # 非副作用工具不进清单


def test_redo_fx_note_skips_failed_steps_and_dedupes():
    from app.api.chat import _redo_fx_note
    note = _redo_fx_note([
        {"tool": "save_download", "args": {"filename": "a.md"}, "is_error": True},   # 失败步不算
        {"tool": "add_questions", "args": {}, "result": "〔题目ID:q1〕", "is_error": False},
        {"tool": "add_questions", "args": {}, "result": "〔题目ID:q2〕", "is_error": False},
    ])
    assert "a.md" not in note
    assert note.count("add_questions") == 1  # 同一工具多次调用只说一次


def test_redo_fx_note_empty_when_no_side_effects():
    from app.api.chat import _redo_fx_note
    assert _redo_fx_note([{"tool": "calculator", "args": {}, "is_error": False}]) == ""
    assert _redo_fx_note([]) == ""


class _FakeDownloadTool(Tool):
    """替身 save_download：只要工具名与 filename 参数对得上即可驱动重做提示。"""
    name = "save_download"
    description = "把内容保存为可下载文件"

    class Params(BaseModel):
        filename: str
        content: str = ""

    async def run(self, params: "_FakeDownloadTool.Params") -> str:
        return f"已保存到下载区：{params.filename}。〔下载ID:d1〕"


class _Recorder:
    """包一层 ModelClient，截下每次调用时模型实际收到的 messages。"""
    def __init__(self, inner) -> None:
        self._inner = inner
        self.seen: list[list] = []

    async def stream(self, messages, tools):
        self.seen.append(list(messages))
        async for c in self._inner.stream(messages, tools):
            yield c


def test_retry_prompt_tells_model_to_redo_the_download(make_mock, tool_turn, text_turn):
    """校验失败重答时，必须告诉模型上一版存的文件已作废、要重存。

    背景（用户实测）：「帮我制定一份 7 天的 AI 学习计划」→ 第一版调了 save_download 存文件，
    但校验没过要重答；重答是全新 RunState，context 只有系统提示+历史+纠正指令，模型看不到
    自己上一版调过 save_download，于是没再存；而交付时 _purge_side_effects 又把第一版那个
    文件删了。两下一叠加，用户最终一个文件都没拿到。
    """
    rec = _Recorder(make_mock([
        tool_turn("save_download", '{"filename":"7天AI学习计划.md","content":"计划正文"}',
                  call_id="d1"),
        text_turn("已为你保存学习计划文件"),        # 第一版终稿 → 校验不通过
        text_turn("修正版：这是学习计划"),           # 重答终稿
    ]))
    reg = ToolRegistry(); reg.register(_FakeDownloadTool())
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=rec, registry=reg, checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="你是助手")
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None,
                    enable_answer_gate=True, answer_gate_max_retries=1)
    verifier = _StubVerifier([
        Verdict(ok=False, failed=["judge"], critique="太笼统", summary="judge"),
        Verdict(ok=True),
    ])
    store = ConversationStore(":memory:")
    app = create_app(config=cfg, harness=harness, store=store,
                     doc_store=DocumentStore(":memory:"), verifier=verifier)
    client = TestClient(app)
    h = _auth(client)
    _run_chat(client, h, "帮我制定一份 7 天的 AI 学习计划")

    # 第 3 次模型调用 = 重答的首次调用；其最后一条用户消息即纠正指令
    assert len(rec.seen) >= 3, "应发生重答"
    corrective = rec.seen[2][-1].content
    assert "太笼统" in corrective                       # 原有的针对性修正意见仍在
    assert "save_download" in corrective                # 点名了上一版调过的工具
    assert "7天AI学习计划.md" in corrective              # 点名了具体文件，模型才知道重做什么
    assert "必须重新调用相应工具再存一次" in corrective


# ---- 兜底：模型没照重做指令执行时，不能让用户空手 ----

def test_split_stale_fx_purges_only_what_delivered_version_remade():
    from app.api.chat import _split_stale_fx
    stale = {"download": ["d1"], "knowledge": ["k1"], "questions": ["q1"]}
    cur = {"download": ["d2"], "knowledge": [], "questions": []}   # 只重做了下载
    purge, keep = _split_stale_fx(stale, cur)
    assert purge == {"download": ["d1"], "knowledge": [], "questions": []}   # 被取代 → 删
    assert keep == {"download": [], "knowledge": ["k1"], "questions": ["q1"]}  # 没重做 → 留


def test_split_stale_fx_purges_all_when_everything_remade():
    from app.api.chat import _split_stale_fx
    purge, keep = _split_stale_fx(
        {"download": ["d1"], "knowledge": [], "questions": []},
        {"download": ["d2"], "knowledge": [], "questions": []})
    assert purge["download"] == ["d1"] and not any(keep.values())


def test_mark_carried_over_keeps_mark_and_notes_provenance():
    from app.api.chat import _mark_carried_over
    steps = [{"tool": "save_download", "result": "已保存：a.md。〔下载ID:d1〕"}]
    _mark_carried_over(steps, {"download": ["d1"], "knowledge": [], "questions": []})
    # 标记必须留着：产物还在，下载按钮得能用
    assert "〔下载ID:d1〕" in steps[0]["result"]
    assert "未通过校验的那一版生成" in steps[0]["result"]


class _StoreSpy:
    """内存版 download_store 替身：真的 SaveDownloadTool 会被注入进来用它，故接口要对得上。"""
    def __init__(self) -> None:
        self.files: dict[str, dict] = {}
        self.deleted: list[str] = []
        self._n = 0

    def create(self, _uid, filename, data, content_type) -> dict:
        self._n += 1
        did = f"d{self._n}"
        rec = {"id": did, "filename": filename, "size": len(data),
               "content_type": content_type}
        self.files[did] = rec
        return rec

    def delete(self, _uid, did) -> bool:
        self.deleted.append(did)
        return self.files.pop(did, None) is not None


def _download_gate_client(turns, verifier, dl_spy):
    reg = ToolRegistry()          # save_download 由 _build_registry 按 download_store 注入
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=turns, registry=reg, checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="你是助手")
    harness.download_store = dl_spy
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None,
                    enable_answer_gate=True, answer_gate_max_retries=1)
    store = ConversationStore(":memory:")
    app = create_app(config=cfg, harness=harness, store=store,
                     doc_store=DocumentStore(":memory:"), verifier=verifier)
    return TestClient(app), store


def test_retry_without_redo_keeps_the_file(make_mock, tool_turn, text_turn):
    """重答没再存文件 → 上一版那个文件必须留下，不能删成两手空空。

    _redo_fx_note 已要求模型重做，但那是提示词、管不住。这是确定性兜底。
    """
    spy = _StoreSpy()
    client, store = _download_gate_client(
        make_mock([
            tool_turn("save_download", '{"filename":"计划.md","content":"正文"}', call_id="d1"),
            text_turn("已保存"),          # 第一版 → 校验不通过
            text_turn("修正版"),          # 重答：没再调 save_download
        ]),
        _StubVerifier([Verdict(ok=False, failed=["judge"], critique="太笼统", summary="judge"),
                       Verdict(ok=True)]),
        spy)
    h = _auth(client)
    cid, _ = _run_chat(client, h, "帮我制定一份 7 天的 AI 学习计划")

    assert spy.deleted == [], "交付版没重做同类产物 → 旧文件不该被删"
    msgs = store.ui_messages(cid)
    steps = [s for m in msgs if m["role"] == "assistant" for s in (m.get("steps") or [])]
    dl = next(s for s in steps if s["tool"] == "save_download")
    assert "〔下载ID:d1〕" in dl["result"]              # 按钮仍可用
    assert "未通过校验的那一版生成" in dl["result"]      # 但已标明出处
    assert "已作废删除" not in dl["result"]


def test_retry_that_redoes_still_purges_the_old_file(make_mock, tool_turn, text_turn):
    """重答重新存了文件 → 旧的仍须删掉，否则并排两个按钮、点旧的是废文件。"""
    spy = _StoreSpy()
    client, store = _download_gate_client(
        make_mock([
            tool_turn("save_download", '{"filename":"计划.md","content":"v1"}', call_id="d1"),
            text_turn("已保存"),
            tool_turn("save_download", '{"filename":"计划.md","content":"v2"}', call_id="d2"),
            text_turn("修正版：已重新保存"),
        ]),
        _StubVerifier([Verdict(ok=False, failed=["judge"], critique="太笼统", summary="judge"),
                       Verdict(ok=True)]),
        spy)
    h = _auth(client)
    cid, _ = _run_chat(client, h, "帮我制定一份 7 天的 AI 学习计划")

    assert spy.deleted == ["d1"], "旧版文件应被删（本版已重新产出）"
    msgs = store.ui_messages(cid)
    steps = [s for m in msgs if m["role"] == "assistant" for s in (m.get("steps") or [])]
    old = steps[0]
    assert "已作废删除" in old["result"] and "〔下载ID:d1〕" not in old["result"]


def test_is_retrieval_tool_covers_web_and_mcp_search():
    """联网检索/抓取类工具须被认作检索依据（喂给 grounding），代码/写库类不算。"""
    from app.api.chat import _is_retrieval_tool
    for yes in ("browse", "http_request",
                "mcp__websearch__bailian_web_search", "mcp__search__foo", "mcp__x__web_lookup"):
        assert _is_retrieval_tool(yes) is True, yes
    for no in ("run_python", "run_shell", "save_download", "remember",
               "sample_questions", "calculator", "search_knowledge", ""):
        # 注：search_knowledge 单独在收集处标 retrieval，不经本谓词
        assert _is_retrieval_tool(no) is False, no


def test_recent_dialogue_skips_tool_noise_and_keeps_order():
    """渲染最近几轮 user/assistant 正文：跳过纯工具调用/工具结果消息，按时间正序。"""
    from app.api.chat import _recent_dialogue
    from harness.types import Message, Role, ToolCall
    hist = [
        Message(role=Role.USER, content="帮我做个方案"),
        Message(role=Role.ASSISTANT, tool_calls=[ToolCall(id="c", name="search_knowledge", arguments={})]),
        Message(role=Role.TOOL, tool_call_id="c", content="检索结果…"),
        Message(role=Role.ASSISTANT, content="请回复 A / B / C 选择方案"),
    ]
    out = _recent_dialogue(hist)
    assert "用户：帮我做个方案" in out
    assert "AI：请回复 A / B / C 选择方案" in out
    assert "检索结果" not in out                       # 工具噪音不进
    assert out.index("帮我做个方案") < out.index("A / B / C")  # 时间正序


def test_recent_dialogue_covers_clarification_detour():
    """单轮不够的正是这种：菜单 → 用户先问澄清 → AI 解释 → 用户才回「A」。
    菜单不在上一条 assistant 里，但仍须落在最近几轮窗口内。"""
    from app.api.chat import _recent_dialogue
    from harness.types import Message, Role
    hist = [
        Message(role=Role.ASSISTANT, content="请回复 A / B / C 选择方案"),
        Message(role=Role.USER, content="C 是什么意思？"),
        Message(role=Role.ASSISTANT, content="C 指的是走缓存方案。"),
    ]
    out = _recent_dialogue(hist)
    assert "A / B / C" in out and "走缓存方案" in out    # 菜单和澄清都在


def test_recent_dialogue_bounded_by_msgs_and_chars():
    from app.api.chat import _recent_dialogue
    from harness.types import Message, Role
    hist = [Message(role=Role.USER if i % 2 == 0 else Role.ASSISTANT, content=f"消息{i}")
            for i in range(20)]
    out = _recent_dialogue(hist, max_msgs=4)
    assert out.count("消息") == 4 and "消息19" in out and "消息0" not in out  # 只保留最近 4 条
    # 字数上限：单条超限时也不会无界增长
    big = [Message(role=Role.ASSISTANT, content="x" * 5000)]
    assert len(_recent_dialogue(big, max_chars=2000)) <= 5000 + 10


def test_recent_dialogue_empty_and_multimodal():
    from app.api.chat import _recent_dialogue
    from harness.types import Message, Role
    assert _recent_dialogue([]) == "" and _recent_dialogue(None) == ""
    hist = [Message(role=Role.ASSISTANT,
                    content=[{"type": "text", "text": "看这两个方案，回 A 或 B"}])]
    assert "回 A 或 B" in _recent_dialogue(hist)


# ---------- 清单收尾：按「计划是谁发的」判断，而非「走没走编排器」 ----------

def test_plan_from_orchestrator_detects_id_bearing_steps():
    """编排器的计划步带 id；模型调 update_plan 发的 ReAct 清单只有 title/status。
    两者同为 scope=plan，必须区分——否则 ReAct 清单会被当成编排器计划跳过收尾，
    永远停在模型最后一次自述的状态，前端渲染成一排 unknown。"""
    from app.api.chat import _plan_from_orchestrator
    import json as _j

    orch = [{"scope": "plan", "text": _j.dumps(
        [{"id": "s1", "title": "查资料", "status": "done"}])}]
    react = [{"scope": "plan", "text": _j.dumps(
        [{"title": "查资料", "status": "running"}])}]

    assert _plan_from_orchestrator(orch) is True
    assert _plan_from_orchestrator(react) is False
    assert _plan_from_orchestrator([]) is False
    assert _plan_from_orchestrator([{"scope": "verify", "text": "x"}]) is False


def test_plan_from_orchestrator_survives_bad_payload():
    """text 不是合法 JSON / 不是数组时不得抛异常——它在交付收尾路径上，崩了会连累整轮。"""
    from app.api.chat import _plan_from_orchestrator
    for bad in ("", "不是JSON", "{}", "null", None):
        assert _plan_from_orchestrator([{"scope": "plan", "text": bad}]) is False


def test_plan_from_orchestrator_uses_last_plan_entry():
    """一轮里可能先后有多条 plan（编排器重规划会再发）；以最后一条为准。"""
    from app.api.chat import _plan_from_orchestrator
    import json as _j
    mixed = [{"scope": "plan", "text": _j.dumps([{"title": "自述", "status": "running"}])},
             {"scope": "plan", "text": _j.dumps([{"id": "s1", "title": "编排", "status": "done"}])}]
    assert _plan_from_orchestrator(mixed) is True

"""端到端：交付提醒只提醒，不改变本轮的成败与内容。

这是这次改动的核心契约——旧交付门会拦下答复、带反馈重答、甚至降级交付；现在无论提醒
多少条，用户拿到的正文一字不改，本轮仍是 done。
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
from app.verify import Notice
from harness.llm.base import StreamChunk
from harness.persistence.checkpoint import CheckpointStore
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink
from harness.tools.base import ToolRegistry


@pytest.fixture(autouse=True)
def _sqlite_allow_cross_thread(monkeypatch):
    orig = sqlite3.connect

    def _patched(*a, **k):
        k["check_same_thread"] = False
        return orig(*a, **k)
    monkeypatch.setattr(sqlite3, "connect", _patched)


ANSWER = "这是最终答复。"


class _Model:
    async def stream(self, messages, tools):
        yield StreamChunk(type="text", text=ANSWER)
        yield StreamChunk(type="done")


class _Checker:
    """按脚本产出提醒；记录被调用时拿到的答复正文。"""
    def __init__(self, notices=(), boom=False) -> None:
        self._notices = list(notices)
        self._boom = boom
        self.seen: list = []

    async def run(self, answer, grounding, registry):
        self.seen.append(answer)
        if self._boom:
            raise RuntimeError("检查器自己崩了")
        return list(self._notices)


def _client(checker=None, **cfg_kw):
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=_Model(), registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj),
                      system_prompt="你是助手")
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None, **cfg_kw)
    app = create_app(config=cfg, harness=harness, store=ConversationStore(":memory:"),
                     doc_store=DocumentStore(":memory:"), delivery_checker=checker)
    return TestClient(app)


def _chat(client, verify=True):
    r = client.post("/api/auth/register",
                    json={"username": "u", "full_name": "测试用户", "password": "pw1234"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    lines = []
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": "问点什么", "verify": verify},
                       headers=h) as resp:
        for line in resp.iter_lines():
            if line.startswith("data: "):
                lines.append(json.loads(line[6:]))
    msgs = client.get(f"/api/conversations/{cid}/messages", headers=h).json()
    return lines, msgs


def _notices(events):
    return [e for e in events
            if e.get("type") == "Progress" and e.get("data", {}).get("scope") == "notice"]


def test_notices_are_streamed_to_the_client():
    checker = _Checker([Notice("format", "回答疑似被截断（代码围栏未闭合）")])
    events, _ = _chat(_client(checker))
    got = _notices(events)
    assert len(got) == 1
    d = got[0]["data"]
    assert d["status"] == "warn", "提醒不能用 error——那会让徽章整条标红，冒充成失败"
    assert d["key"] == "notice:format" and d["detail"]["label"] == "完整性"


def test_answer_is_untouched_and_turn_still_succeeds():
    """有提醒也不重答、不改正文、不判失败。"""
    checker = _Checker([Notice("code", "代码未跑通：run_python: 报错")])
    _, msgs = _chat(_client(checker))
    last = msgs[-1]
    assert last["content"] == ANSWER          # 正文一字未改
    assert last["status"] == "done"           # 本轮仍是成功


def test_checker_sees_the_delivered_answer():
    checker = _Checker()
    _chat(_client(checker))
    assert checker.seen == [ANSWER]


def test_checker_crash_does_not_break_delivery():
    """检查器自己崩了，答复照常交付——它是旁路，绝不能影响主链路。"""
    _, msgs = _chat(_client(_Checker(boom=True)))
    assert msgs[-1]["content"] == ANSWER and msgs[-1]["status"] == "done"


def test_no_checker_means_no_notices():
    """没装配 checker（enable_delivery_checks=false）→ 一条提醒都不发。"""
    events, msgs = _chat(_client(None, enable_delivery_checks=False))
    assert _notices(events) == [] and msgs[-1]["content"] == ANSWER


def test_verify_toggle_off_skips_checks():
    """用户关掉聊天页「结果校验」→ 整轮不提醒（与轨迹质量分同口径）。"""
    checker = _Checker([Notice("format", "截断")])
    events, _ = _chat(_client(checker), verify=False)
    assert checker.seen == [] and _notices(events) == []


def test_notices_are_recorded_for_stats():
    """提醒记进 verify 列，统计页据此看「哪一项最常提醒」。"""
    checker = _Checker([Notice("facts", "引用链接不可达：http://x")])
    _, msgs = _chat(_client(checker))
    vt = msgs[-1].get("verify")
    assert vt and vt.get("notices") == ["facts"]


def test_message_marked_done_before_slow_delivery_checks():
    """回归「刷新后耗时从1重计」：交付检查慢时，消息也必须在检查前就翻成 done。

    模拟场景——交付检查里跑代码卡了很久。旧行为：这期间消息一直 streaming，用户刷新
    → 前端把它当在途 run 接回、耗时归零重计。修法：答案交付即 mark_delivered 翻 done，
    故检查跑到一半刷新，DB 里已是 done + 冻结耗时。
    """
    import time as _t

    seen_status_at_check = {}

    class _SlowChecker:
        def __init__(self, store, cid):
            self._store = store
            self._cid = cid

        async def run(self, answer, grounding, registry):
            # 检查执行的这一刻，去库里看消息 status——必须已经是 done（不是 streaming）
            rows = self._store._conn.execute(
                "SELECT status FROM conversation_messages "
                "WHERE role='assistant' ORDER BY seq DESC LIMIT 1").fetchone()
            seen_status_at_check["status"] = rows[0] if rows else None
            return []

    # 需要拿到 create_app 内部用的 store，故显式注入
    from app.assembly import Harness
    from harness.persistence.checkpoint import CheckpointStore
    from harness.persistence.trajectory import TrajectoryStore, TrajectorySink
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=_Model(), registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="s")
    store = ConversationStore(":memory:")
    checker = _SlowChecker(store, None)
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None)
    app = create_app(config=cfg, harness=harness, store=store,
                     doc_store=DocumentStore(":memory:"), delivery_checker=checker)
    client = TestClient(app)
    r = client.post("/api/auth/register",
                    json={"username": "u", "full_name": "测试", "password": "pw1234"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": "问", "verify": True},
                       headers=h) as resp:
        for _ in resp.iter_lines():
            pass
    # 交付检查执行时，消息已是 done —— 这就是「刷新不再重计」的保证
    assert seen_status_at_check.get("status") == "done"
    last = client.get(f"/api/conversations/{cid}/messages", headers=h).json()[-1]
    assert last["status"] == "done" and last["elapsed_ms"] is not None

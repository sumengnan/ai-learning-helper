"""抓取失败网址登记的端到端接线：经 /api/chat 真实工具循环，失败登记须跨轮生效。"""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.assembly import Harness
from app.config import AppConfig
from app.conversations import ConversationStore
from app.documents import DocumentStore
from app.main import create_app
from app.url_blocklist import UrlBlockStore
from harness.persistence.checkpoint import CheckpointStore
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink
from harness.tools.base import Tool, ToolRegistry


@pytest.fixture(autouse=True)
def _sqlite_allow_cross_thread(monkeypatch):
    orig = sqlite3.connect

    def _patched(*a, **k):
        k["check_same_thread"] = False
        return orig(*a, **k)
    monkeypatch.setattr(sqlite3, "connect", _patched)


class _FakeHttp(Tool):
    name = "http_request"
    description = "抓取网页"

    class Params(BaseModel):
        url: str

    def __init__(self, result: str):
        self._result = result
        self.calls: list[str] = []

    async def run(self, params):
        self.calls.append(params.url)
        return self._result


def _app(http_tool, blocked_store=None, **cfg_kw):
    reg = ToolRegistry(); reg.register(http_tool)
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=None, registry=reg,
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj),
                      system_prompt="你是助手")
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None, **cfg_kw)
    return harness, cfg


def _client(make_mock, turns, http_tool, **cfg_kw):
    harness, cfg = _app(http_tool, **cfg_kw)
    harness.client = make_mock(turns)
    store = ConversationStore(":memory:")
    app = create_app(config=cfg, harness=harness, store=store,
                     doc_store=DocumentStore(":memory:"))
    return TestClient(app), store


def _auth(client):
    r = client.post("/api/auth/register", json={"username": "u", "password": "pw1234"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _chat(client, h, cid, msg):
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": msg}, headers=h) as r:
        return [json.loads(l[6:]) for l in r.iter_lines() if l and l.startswith("data: ")]


def _tool_results(events):
    return [e["data"]["result"] for e in events if e["type"] == "ToolFinished"]


def test_dead_url_is_recorded_then_skipped_on_next_turn(make_mock, tool_turn, text_turn):
    """第一轮撞 404 → 登记；第二轮模型又想抓同一个 → 不发请求，直接告诉它换一个。"""
    http = _FakeHttp("HTTP 404\nNot Found")
    args = json.dumps({"url": "https://dead.example.com/page"})
    client, _ = _client(
        make_mock,
        [tool_turn("http_request", args), text_turn("第一轮：没抓到"),      # 轮1：抓→404
         tool_turn("http_request", args), text_turn("第二轮：换个来源")],   # 轮2：又想抓同一个
        http)
    h = _auth(client)
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]

    ev1 = _chat(client, h, cid, "查一下")
    assert any("HTTP 404" in r["content"] for r in _tool_results(ev1))

    ev2 = _chat(client, h, cid, "再查一下")
    r2 = _tool_results(ev2)
    assert r2 and r2[0]["is_error"] is True                 # 变成显式失败，模型必须处理
    assert "请改用其它网址或来源" in r2[0]["content"]
    assert http.calls == ["https://dead.example.com/page"]  # 全程只真发过 1 次请求


def test_403_skips_whole_domain_on_next_turn(make_mock, tool_turn, text_turn):
    http = _FakeHttp("HTTP 403\nForbidden")
    client, _ = _client(
        make_mock,
        [tool_turn("http_request", json.dumps({"url": "https://blocked.com/a"})),
         text_turn("抓不到"),
         tool_turn("http_request", json.dumps({"url": "https://blocked.com/b"})),
         text_turn("还是抓不到")],
        http)
    h = _auth(client)
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]

    _chat(client, h, cid, "查 a")
    r2 = _tool_results(_chat(client, h, cid, "查 b"))
    assert r2[0]["is_error"] is True and "该域名" in r2[0]["content"]
    assert http.calls == ["https://blocked.com/a"]          # 同域另一页也没白跑


def test_successful_fetch_is_never_blocked(make_mock, tool_turn, text_turn):
    http = _FakeHttp("HTTP 200\n标题：好页\n最终URL：https://ok.com/a\n\n正经正文")
    args = json.dumps({"url": "https://ok.com/a"})
    client, _ = _client(
        make_mock,
        [tool_turn("http_request", args), text_turn("拿到了"),
         tool_turn("http_request", args), text_turn("又拿到了")],
        http)
    h = _auth(client)
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]

    _chat(client, h, cid, "查")
    r2 = _tool_results(_chat(client, h, cid, "再查"))
    assert r2[0]["is_error"] is False
    assert len(http.calls) == 2                             # 成功的源照抓不误


def test_blocklist_shared_across_users(make_mock, tool_turn, text_turn):
    """死链是网站的属性、不是用户的属性：A 撞过的坑 B 不必再撞。"""
    http = _FakeHttp("HTTP 404\nNot Found")
    args = json.dumps({"url": "https://dead.com/x"})
    client, _ = _client(
        make_mock,
        [tool_turn("http_request", args), text_turn("A 没抓到"),
         tool_turn("http_request", args), text_turn("B 也想抓")],
        http)
    ha = _auth(client)
    cid_a = client.post("/api/conversations", json={}, headers=ha).json()["id"]
    _chat(client, ha, cid_a, "A 查")

    rb = client.post("/api/auth/register", json={"username": "b", "password": "pw1234"})
    hb = {"Authorization": f"Bearer {rb.json()['token']}"}
    cid_b = client.post("/api/conversations", json={}, headers=hb).json()["id"]
    r = _tool_results(_chat(client, hb, cid_b, "B 查"))
    assert r[0]["is_error"] is True
    assert http.calls == ["https://dead.com/x"]


def test_disabled_by_config_is_passthrough(make_mock, tool_turn, text_turn):
    http = _FakeHttp("HTTP 404\nNot Found")
    args = json.dumps({"url": "https://dead.com/x"})
    client, _ = _client(
        make_mock,
        [tool_turn("http_request", args), text_turn("一"),
         tool_turn("http_request", args), text_turn("二")],
        http, enable_url_blocklist=False)
    h = _auth(client)
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    _chat(client, h, cid, "查")
    r2 = _tool_results(_chat(client, h, cid, "再查"))
    assert r2[0]["is_error"] is False and len(http.calls) == 2   # 关了就照旧撞

"""思考模式的作用域：聊天页那个开关只该管主循环，不该漏给旁路调用。

此前 set_extra_body_override 设在 gen() 里且从不 reset，于是交付门校验、记忆调和、
记忆整合全都悄悄继承了它——那个开关的语义是「我这个问题不用想那么久」，与它们无关。
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.assembly import Harness
from app.config import AppConfig
from app.conversations import ConversationStore
from app.documents import DocumentStore
from app.main import create_app
from harness.llm.base import StreamChunk
from harness.llm.openai_compat import get_extra_body_override
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


class _Probe:
    """每次模型调用都记下当时生效的 enable_thinking（即真会发出去的那份）。"""
    def __init__(self) -> None:
        self.seen: list = []

    async def stream(self, messages, tools):
        self.seen.append(get_extra_body_override().get("enable_thinking"))
        yield StreamChunk(type="text", text="答案")
        yield StreamChunk(type="done")


class _SpyChecker:
    """在交付检查期间探一次 contextvar：grounding 真正跑的地方就在这一层之下。"""
    def __init__(self) -> None:
        self.thinking_at_verify: list = []

    async def run(self, answer, grounding, registry):
        self.thinking_at_verify.append(get_extra_body_override().get("enable_thinking"))
        return []


def _client(probe, checker=None, **cfg_kw):
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=probe, registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj),
                      system_prompt="你是助手")
    cfg = AppConfig(api_key="k", model="qwen-max", app_db_path=":memory:",
                    _env_file=None, **cfg_kw)
    app = create_app(config=cfg, harness=harness, store=ConversationStore(":memory:"),
                     doc_store=DocumentStore(":memory:"), delivery_checker=checker)
    return TestClient(app)


def _chat(client, *, think: bool):
    r = client.post("/api/auth/register", json={"username": "u", "full_name": "测试用户", "password": "pw1234"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": "你好", "think": think},
                       headers=h) as resp:
        for _ in resp.iter_lines():
            pass


@pytest.mark.parametrize("think", [True, False])
def test_main_loop_follows_the_chat_toggle(think):
    """主循环仍须跟随开关——这是它唯一该管的地方。"""
    probe = _Probe()
    _chat(_client(probe), think=think)
    assert probe.seen and probe.seen[0] is think


@pytest.mark.parametrize("think", [True, False])
def test_delivery_checks_do_not_inherit_the_chat_toggle(think):
    """交付检查不该继承聊天页开关：用户关的是「我这个问题不用想那么久」，
    不是「检查别检查了」。grounding 那档自己显式声明思考（核对档恒关）。"""
    probe, checker = _Probe(), _SpyChecker()
    _chat(_client(probe, checker=checker), think=think)
    assert checker.thinking_at_verify, "交付检查应跑过"
    assert checker.thinking_at_verify[0] is None, (
        f"检查时不该带 enable_thinking，却拿到 {checker.thinking_at_verify[0]}")


def test_toggle_is_reset_after_the_loop():
    """pump() 收尾须还原：否则 gen() 里后续的记忆调和/整合又会继承它。"""
    probe = _Probe()
    _chat(_client(probe), think=False)
    assert get_extra_body_override().get("enable_thinking") is None

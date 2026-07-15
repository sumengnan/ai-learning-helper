"""系统提示的条件注入：截下模型每轮实际收到的 system 消息来断言，而非只查常量。"""
import io
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.assembly import Harness
from app.config import AppConfig
from app.conversations import ConversationStore
from app.documents import DocumentStore
from app.main import create_app
from harness.llm.base import StreamChunk
from harness.persistence.checkpoint import CheckpointStore
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink
from harness.tools.base import ToolRegistry


@pytest.fixture(autouse=True)
def _sqlite_allow_cross_thread(monkeypatch):
    orig = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect",
                        lambda *a, **k: orig(*a, **{**k, "check_same_thread": False}))


class _PromptRecordingClient:
    """记录模型每轮实际收到的 system 消息与工具名。"""

    def __init__(self):
        self.systems: list[str] = []
        self.tools: list[list[str]] = []

    async def stream(self, messages, tools):
        self.systems.append(next((m.content or "" for m in messages
                                  if getattr(m.role, "value", m.role) == "system"), ""))
        self.tools.append([t["function"]["name"] for t in tools])
        yield StreamChunk(type="text", text="好的。")
        yield StreamChunk(type="done")


def _client(rec, tmp_path, **cfg_kw):
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=rec, registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj),
                      system_prompt="你是助手")
    # attachments_dir 必须指向 tmp_path：默认是相对路径，会把测试上传的裸字节落进仓库
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None,
                    attachments_dir=str(tmp_path / "attachments"), **cfg_kw)
    app = create_app(config=cfg, harness=harness, store=ConversationStore(":memory:"),
                     doc_store=DocumentStore(":memory:"))
    return TestClient(app)


def _auth(c):
    r = c.post("/api/auth/register", json={"username": "u", "password": "pw1234"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _chat(c, h, cid, msg="问个问题", **body):
    with c.stream("POST", "/api/chat",
                  json={"conversation_id": cid, "message": msg, **body}, headers=h) as r:
        list(r.iter_lines())


def test_attachment_guide_absent_without_attachments(tmp_path):
    """没附件时不该介绍 list_attachments/read_attachment —— 那两个工具根本没注册，
    介绍一批模型没有的工具比浪费 token 更糟。"""
    rec = _PromptRecordingClient()
    c = _client(rec, tmp_path)
    h = _auth(c)
    cid = c.post("/api/conversations", json={}, headers=h).json()["id"]
    _chat(c, h, cid)

    sys_prompt = rec.systems[0]
    assert "list_attachments" not in sys_prompt
    assert "read_attachment" not in sys_prompt
    assert "list_attachments" not in rec.tools[0]      # 指引与工具同进同出
    assert "read_attachment" not in rec.tools[0]


def test_attachment_guide_present_with_attachment(tmp_path):
    rec = _PromptRecordingClient()
    c = _client(rec, tmp_path)
    h = _auth(c)
    cid = c.post("/api/conversations", json={}, headers=h).json()["id"]
    up = c.post(f"/api/conversations/{cid}/attachments", headers=h,
                files={"file": ("note.txt", io.BytesIO(b"hi"), "text/plain")})
    assert up.status_code == 200, up.text
    _chat(c, h, cid, attachment_ids=[up.json()["id"]])

    sys_prompt = rec.systems[0]
    assert "list_attachments" in sys_prompt and "read_attachment" in sys_prompt
    assert "list_attachments" in rec.tools[0]          # 有附件 → 指引与工具都在


def test_always_resident_guides_are_present(tmp_path):
    """这些不能做成按需加载的技能：模型不会为了知道自己不许撒谎而去 load_skill，
    也不知道自己不知道今天几号。"""
    rec = _PromptRecordingClient()
    c = _client(rec, tmp_path)
    h = _auth(c)
    cid = c.post("/api/conversations", json={}, headers=h).json()["id"]
    _chat(c, h, cid)

    sys_prompt = rec.systems[0]
    assert "【当前日期】" in sys_prompt                 # _today_guide：模型无从自知
    assert "绝不能说" in sys_prompt                     # 假承诺护栏
    assert "无条件" in sys_prompt                       # 答错必存
    assert "参考来源" in sys_prompt                     # 引用约定

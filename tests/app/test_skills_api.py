"""技能详情端点：前端展开「已启用技能…」时按需取 md 正文。

正文不随 skill 进度事件下发——它是静态资源（随部署固定，2-3KB/个），塞进事件就会连同
progress 列一起，在每条命中技能的助手消息里各存一份。
"""
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
from harness.skills.registry import SkillRegistry
from harness.tools.base import ToolRegistry


@pytest.fixture(autouse=True)
def _sqlite_allow_cross_thread(monkeypatch):
    orig = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect",
                        lambda *a, **k: orig(*a, **{**k, "check_same_thread": False}))


def _skills_dir(tmp_path):
    d = tmp_path / "skills" / "demo-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: 演示技能\ntriggers: 演示, 示范\n---\n"
        "# 演示技能\n\n## 步骤\n\n1. **第一步**：做点什么\n",
        encoding="utf-8")
    return str(tmp_path / "skills")


def _client(make_mock, tmp_path, *, with_skills=True):
    traj = TrajectoryStore(":memory:")
    reg = SkillRegistry(_skills_dir(tmp_path)) if with_skills else None
    h = Harness(client=make_mock([]), registry=ToolRegistry(),
                checkpoint_store=CheckpointStore(":memory:"),
                trajectory_store=traj, sink=TrajectorySink(traj),
                system_prompt="你是助手", skill_registry=reg)
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None)
    app = create_app(config=cfg, harness=h, store=ConversationStore(":memory:"),
                     doc_store=DocumentStore(":memory:"))
    return TestClient(app)


def _auth(c):
    r = c.post("/api/auth/register", json={"username": "u", "password": "pw1234"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_get_skill_returns_markdown_body(make_mock, tmp_path):
    c = _client(make_mock, tmp_path)
    r = c.get("/api/skills/demo-skill", headers=_auth(c))
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "demo-skill"
    assert body["description"] == "演示技能"
    # 正文是去掉 frontmatter 的 SKILL.md 原文，供前端按 markdown 渲染
    assert body["body"].startswith("# 演示技能")
    assert "**第一步**" in body["body"]
    assert "triggers:" not in body["body"]      # frontmatter 不外泄


def test_get_missing_skill_404(make_mock, tmp_path):
    """历史消息里的技能可能已被删除或改名——要给 404，前端才能提示而不是干转圈。"""
    c = _client(make_mock, tmp_path)
    r = c.get("/api/skills/no-such-skill", headers=_auth(c))
    assert r.status_code == 404


def test_list_skills(make_mock, tmp_path):
    c = _client(make_mock, tmp_path)
    r = c.get("/api/skills", headers=_auth(c))
    assert r.status_code == 200
    assert [s["name"] for s in r.json()["skills"]] == ["demo-skill"]


def test_skill_endpoints_require_auth(make_mock, tmp_path):
    c = _client(make_mock, tmp_path)
    assert c.get("/api/skills/demo-skill").status_code in (401, 403)


def test_routes_absent_without_skill_registry(make_mock, tmp_path):
    """没配技能目录时不挂载这些路由（harness.skill_registry 为 None）。

    判据不能只看 content-type：未注册的路径会落到 main.py 末尾的 SPA catch-all
    （GET /{full_path:path}），而**测试环境没有前端构建产物**（web/dist/index.html 不存在），
    catch-all 因此抛 404、由 FastAPI 以 JSON 返回错误——content-type 同样是 application/json。
    真正该判的是「拿到的不是一份技能详情」。
    """
    c = _client(make_mock, tmp_path, with_skills=False)
    r = c.get("/api/skills/demo-skill", headers=_auth(c))
    body = r.json() if "application/json" in r.headers.get("content-type", "") else {}
    assert "body" not in body and "description" not in body, "不该返回技能详情"

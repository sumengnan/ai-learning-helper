# tests/app/test_pending_actions.py
"""破坏性操作的两段式确认：agent 只登记，用户确认后由 API 执行。"""
import pytest

from app.pending_actions import (
    CONFIRMED, EXPIRED, PENDING, PendingActionStore, REJECTED)
from app.questions import QuestionStore
from app.tools.exam_tools import DeleteQuestionsTool


def _stores():
    q = QuestionStore(":memory:")
    p = PendingActionStore(conn=q._db)      # 同库，省得开两个 :memory:
    return q, p


def _add(q, uid, stem):
    return q.create(uid, {"type": "single", "stem": stem,
                          "options": ["A", "B"], "answer": 0})


# ---------- 工具侧：登记而非删除 ----------

async def test_delete_tool_registers_instead_of_deleting():
    """核心不变式：调用删除工具后，题目必须还在——它只登记，不执行。"""
    q, p = _stores()
    qid = _add(q, "u1", "什么是自注意力机制？")
    out = await DeleteQuestionsTool(q, "u1", p).run(
        DeleteQuestionsTool.Params(question_ids=[qid]))

    assert q.get("u1", qid) is not None, "工具不得直接删除"
    assert "等待用户确认" in out and "尚未删除" in out
    assert "什么是自注意力机制？" in out          # 列题干给用户看，不是 id
    assert "〔待确认:" in out                     # 机读标记供前端渲染确认卡片
    assert len(p.list_pending("u1")) == 1


async def test_delete_tool_reports_no_match_without_registering():
    q, p = _stores()
    out = await DeleteQuestionsTool(q, "u1", p).run(
        DeleteQuestionsTool.Params(question_ids=["nope"]))
    assert "没有匹配的题目" in out
    assert p.list_pending("u1") == []          # 空请求不该留下待确认垃圾


async def test_delete_tool_falls_back_when_no_pending_store():
    """未接确认机制的装配路径（老测试/精简装配）维持原直删行为，不静默失效。"""
    q, _ = _stores()
    qid = _add(q, "u1", "题")
    out = await DeleteQuestionsTool(q, "u1", None).run(
        DeleteQuestionsTool.Params(question_ids=[qid]))
    assert "已从题库删除" in out and q.get("u1", qid) is None


# ---------- store 侧：一次性、过期、归属 ----------

def test_confirm_is_single_shot():
    """连点两次确认只能生效一次——这是删除不被执行两次的唯一保证。"""
    _, p = _stores()
    pid = p.create("u1", "c1", "delete_questions", {"ids": ["a", "b"]})
    assert p.decide("u1", pid, CONFIRMED) is not None
    assert p.decide("u1", pid, CONFIRMED) is None      # 第二次拿不到 → 调用方不执行
    assert p.decide("u1", pid, REJECTED) is None       # 也不能改判


def test_other_user_cannot_see_or_decide():
    _, p = _stores()
    pid = p.create("u1", "c1", "delete_questions", {"ids": ["a"]})
    assert p.get("u2", pid) is None                    # 不泄露「存在但不属于你」
    assert p.decide("u2", pid, CONFIRMED) is None
    assert p.get("u1", pid)["status"] == PENDING       # 未被他人影响


def test_expired_cannot_be_confirmed():
    _, p = _stores()
    pid = p.create("u1", "c1", "delete_questions", {"ids": ["a"]}, ttl_seconds=-1)
    assert p.get("u1", pid)["status"] == EXPIRED
    assert p.decide("u1", pid, CONFIRMED) is None
    assert p.list_pending("u1") == []                  # 过期的不再列出


def test_purge_expired_removes_only_stale_pending():
    _, p = _stores()
    live = p.create("u1", "c1", "delete_questions", {"ids": ["a"]})
    p.create("u1", "c1", "delete_questions", {"ids": ["b"]}, ttl_seconds=-1)
    assert p.purge_expired() == 1
    assert [a["id"] for a in p.list_pending("u1")] == [live]


def test_list_pending_scoped_by_conversation():
    _, p = _stores()
    a = p.create("u1", "c1", "delete_questions", {"ids": ["a"]})
    p.create("u1", "c2", "delete_questions", {"ids": ["b"]})
    assert [x["id"] for x in p.list_pending("u1", "c1")] == [a]
    assert len(p.list_pending("u1")) == 2


def test_decide_rejects_unknown_status():
    _, p = _stores()
    pid = p.create("u1", "c1", "delete_questions", {"ids": ["a"]})
    with pytest.raises(ValueError):
        p.decide("u1", pid, "whatever")


# ---------- API 端到端：确认后才真正执行 ----------

from fastapi import FastAPI                                    # noqa: E402
from fastapi.testclient import TestClient                      # noqa: E402

from app.api.pending_actions import make_pending_actions_router  # noqa: E402
from app.auth import current_user                               # noqa: E402


def _api(q, p):
    app = FastAPI()
    app.include_router(make_pending_actions_router(p, q, None))
    app.dependency_overrides[current_user] = lambda: "u1"
    return TestClient(app)


def test_confirm_executes_the_delete():
    q, p = _stores()
    qid = _add(q, "u1", "待删的题")
    pid = p.create("u1", "c1", "delete_questions", {"ids": [qid], "labels": ["待删的题"]})
    c = _api(q, p)

    assert q.get("u1", qid) is not None            # 确认前还在
    r = c.post(f"/api/pending-actions/{pid}/confirm")
    assert r.status_code == 200 and r.json()["deleted"] == 1
    assert q.get("u1", qid) is None                # 确认后才没了


def test_double_confirm_does_not_delete_twice():
    """回归：若先执行删除再迁移状态，重复点击就会删两次。顺序必须是先原子迁移、后执行。"""
    q, p = _stores()
    qid = _add(q, "u1", "题")
    pid = p.create("u1", "c1", "delete_questions", {"ids": [qid]})
    c = _api(q, p)
    assert c.post(f"/api/pending-actions/{pid}/confirm").status_code == 200
    r2 = c.post(f"/api/pending-actions/{pid}/confirm")
    assert r2.status_code == 409 and "已处理过" in r2.json()["detail"]


def test_reject_leaves_data_untouched():
    q, p = _stores()
    qid = _add(q, "u1", "保住的题")
    pid = p.create("u1", "c1", "delete_questions", {"ids": [qid]})
    c = _api(q, p)
    assert c.post(f"/api/pending-actions/{pid}/reject").status_code == 200
    assert q.get("u1", qid) is not None
    assert c.post(f"/api/pending-actions/{pid}/confirm").status_code == 409   # 拒了不能再确认


def test_expired_confirm_returns_409():
    q, p = _stores()
    qid = _add(q, "u1", "题")
    pid = p.create("u1", "c1", "delete_questions", {"ids": [qid]}, ttl_seconds=-1)
    r = _api(q, p).post(f"/api/pending-actions/{pid}/confirm")
    assert r.status_code == 409 and "过期" in r.json()["detail"]
    assert q.get("u1", qid) is not None


def test_list_returns_labels_not_internal_ids():
    q, p = _stores()
    p.create("u1", "c1", "delete_questions", {"ids": ["x1"], "labels": ["什么是注意力"]})
    items = _api(q, p).get("/api/pending-actions").json()
    assert items[0]["labels"] == ["什么是注意力"] and items[0]["count"] == 1
    assert "user_id" not in items[0] and "payload" not in items[0]


def test_unknown_kind_is_refused():
    """未知 kind 宁可不做，不可乱做。"""
    q, p = _stores()
    pid = p.create("u1", "c1", "drop_everything", {"ids": ["x"]})
    assert _api(q, p).post(f"/api/pending-actions/{pid}/confirm").status_code == 400

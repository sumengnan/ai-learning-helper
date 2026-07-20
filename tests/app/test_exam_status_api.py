# tests/app/test_exam_status_api.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.exam_status import make_exam_router
from app.auth import current_user
from app.exam_session import ExamSessionStore


def _client(store) -> TestClient:
    app = FastAPI()
    app.include_router(make_exam_router(store))
    app.dependency_overrides[current_user] = lambda: "u1"   # 绕过鉴权
    return TestClient(app)


def _q(ans=1):
    return {"type": "single", "stem": "Q", "options": ["a", "b"], "answer": ans, "explanation": ""}


def test_status_reports_active_exam_progress():
    es = ExamSessionStore(":memory:")
    es.start("u1", "c1", [_q(), _q(), _q()], "graded")
    body = _client(es).get("/api/exam/status?conversation_id=c1").json()
    assert body == {"active": True, "cursor": 0, "total": 3, "mode": "graded", "type": "single"}


def test_status_advances_cursor_after_answering():
    es = ExamSessionStore(":memory:")
    es.start("u1", "c1", [_q(), _q()], "instant")
    es.record("u1", "c1", 1, True)                       # 答完第 1 题，游标推进
    body = _client(es).get("/api/exam/status?conversation_id=c1").json()
    assert body["active"] is True and body["cursor"] == 1 and body["total"] == 2


def test_status_inactive_when_no_exam():
    es = ExamSessionStore(":memory:")
    body = _client(es).get("/api/exam/status?conversation_id=c1").json()
    assert body == {"active": False}


def test_status_inactive_after_all_answered():
    es = ExamSessionStore(":memory:")
    es.start("u1", "c1", [_q()], "instant")
    es.record("u1", "c1", 1, True)                       # 唯一一题答完 → 游标越界（已答完）
    body = _client(es).get("/api/exam/status?conversation_id=c1").json()
    assert body == {"active": False}


def test_status_inactive_when_store_missing():
    body = _client(None).get("/api/exam/status?conversation_id=c1").json()
    assert body == {"active": False}


def test_status_isolated_per_conversation():
    es = ExamSessionStore(":memory:")
    es.start("u1", "c1", [_q()], "instant")
    body = _client(es).get("/api/exam/status?conversation_id=other").json()
    assert body == {"active": False}                     # 另一会话无考试

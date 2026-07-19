# app/api/exam_status.py
"""考试状态查询：前端据此显示「考试中」标识（当前第几题/共几题/模式）。

只读——判分、答错入库、游标推进仍由 /api/chat 判分中间件确定性负责。用户隔离。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import current_user
from ..exam_session import ExamSessionStore


def make_exam_router(exam_session_store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/exam/status")
    async def exam_status(conversation_id: str,
                          user_id: str = Depends(current_user)) -> dict:
        """本会话是否有进行中的考试。无考试/已答完/未装配考试存储 → {active: False}。"""
        if exam_session_store is None:
            return {"active": False}
        s = exam_session_store.get_active(user_id, conversation_id)
        if s is None or ExamSessionStore.is_finished(s):
            return {"active": False}
        cur = ExamSessionStore.current(s)
        return {"active": True, "cursor": s["cursor"], "total": len(s["questions"]),
                "mode": s["mode"], "type": cur["type"] if cur else None}

    return router

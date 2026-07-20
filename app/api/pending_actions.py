# app/api/pending_actions.py
"""待确认的破坏性操作：列出 / 确认执行 / 拒绝。

真正的删除在这里发生，不在 agent 那边——所以不需要给计划做快照或断点续跑。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..pending_actions import CONFIRMED, EXPIRED, PENDING, REJECTED
from ..auth import current_user

log = logging.getLogger("app.pending")


def make_pending_actions_router(pending_store, question_store, wrong_store) -> APIRouter:
    router = APIRouter()

    def _execute(action: dict) -> dict:
        """按 kind 执行已确认的动作。未知 kind 一律拒绝执行（宁可不做，不可乱做）。"""
        kind = action["kind"]
        ids = list((action.get("payload") or {}).get("ids") or [])
        if kind == "delete_questions":
            if question_store is None:
                raise HTTPException(status_code=503, detail="题库未启用")
            question_store.delete_many(action["user_id"], ids)
            return {"deleted": len(ids), "kind": kind}
        if kind == "delete_wrong_answers":
            if wrong_store is None:
                raise HTTPException(status_code=503, detail="错题集未启用")
            wrong_store.delete_many(action["user_id"], ids)
            return {"deleted": len(ids), "kind": kind}
        raise HTTPException(status_code=400, detail=f"不支持的操作类型：{kind}")

    def _public(a: dict) -> dict:
        """对外形状：不外泄 user_id。payload 只给前端渲染需要的 labels/数量。"""
        p = a.get("payload") or {}
        return {"id": a["id"], "kind": a["kind"], "status": a["status"],
                "count": len(p.get("ids") or []), "labels": p.get("labels") or [],
                "created_at": a["created_at"], "expires_at": a["expires_at"]}

    @router.get("/api/pending-actions")
    def list_pending(conversation_id: str | None = None,
                     user_id: str = Depends(current_user)):
        if pending_store is None:
            return []
        return [_public(a) for a in pending_store.list_pending(user_id, conversation_id)]

    @router.get("/api/pending-actions/{pid}")
    def get_pending(pid: str, user_id: str = Depends(current_user)):
        if pending_store is None:
            raise HTTPException(status_code=404, detail="待确认操作不存在")
        a = pending_store.get(user_id, pid)
        if a is None:
            raise HTTPException(status_code=404, detail="待确认操作不存在")
        return _public(a)

    @router.post("/api/pending-actions/{pid}/confirm")
    def confirm(pid: str, user_id: str = Depends(current_user)):
        if pending_store is None:
            raise HTTPException(status_code=404, detail="待确认操作不存在")
        # decide 是原子的：连点两次只有第一次拿到记录，第二次得到 None。
        # 删除必须在这之后执行，顺序反过来就会重复删。
        action = pending_store.decide(user_id, pid, CONFIRMED)
        if action is None:
            cur = pending_store.get(user_id, pid)
            if cur is None:
                raise HTTPException(status_code=404, detail="待确认操作不存在")
            if cur["status"] == EXPIRED:
                raise HTTPException(status_code=409, detail="该操作已过期，请重新发起")
            raise HTTPException(status_code=409, detail=f"该操作已处理过（{cur['status']}）")
        result = _execute(action)
        log.info("已执行确认操作 %s kind=%s 影响 %d 项", pid, action["kind"], result["deleted"])
        return {"ok": True, **result}

    @router.post("/api/pending-actions/{pid}/reject")
    def reject(pid: str, user_id: str = Depends(current_user)):
        if pending_store is None:
            raise HTTPException(status_code=404, detail="待确认操作不存在")
        action = pending_store.decide(user_id, pid, REJECTED)
        if action is None:
            cur = pending_store.get(user_id, pid)
            if cur is None:
                raise HTTPException(status_code=404, detail="待确认操作不存在")
            if cur["status"] == PENDING:      # 理论不可达，兜底
                raise HTTPException(status_code=409, detail="操作状态异常，请重试")
            raise HTTPException(status_code=409, detail=f"该操作已处理过（{cur['status']}）")
        return {"ok": True, "kind": action["kind"]}

    return router

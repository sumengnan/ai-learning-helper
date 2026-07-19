# app/api/stats.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth import current_user


class _DeleteIds(BaseModel):
    ids: list[str]


def make_stats_router(stats_service) -> APIRouter:
    router = APIRouter()

    @router.get("/api/stats/overview")
    async def overview(days: int = Query(14, ge=1, le=90),
                       user_id: str = Depends(current_user)):
        return stats_service.overview(user_id, days=days)

    @router.get("/api/stats/memory")
    async def memory(limit: int = Query(50, ge=1, le=1000),
                     user_id: str = Depends(current_user)):
        return stats_service.memory_items(user_id, limit)

    @router.delete("/api/stats/memory/{mem_id}")
    async def delete_memory(mem_id: str, user_id: str = Depends(current_user)):
        if not stats_service.delete_memory(user_id, mem_id):
            raise HTTPException(status_code=404, detail="记忆不存在或无权删除")
        return {"ok": True}

    @router.post("/api/stats/memory/delete")
    async def delete_memories(body: _DeleteIds, user_id: str = Depends(current_user)):
        """批量删除偏好，返回实际删除的 id 列表（非本人/不存在的自动跳过）。"""
        return {"deleted": stats_service.delete_memories(user_id, body.ids)}

    @router.post("/api/stats/memory/consolidate")
    async def consolidate_memory(user_id: str = Depends(current_user)):
        """手动「整理相似偏好」：把同主题的多条偏好合并成一条，返回合并统计。"""
        return await stats_service.consolidate_memories(user_id)

    return router

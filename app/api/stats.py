# app/api/stats.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import current_user


def make_stats_router(stats_service) -> APIRouter:
    router = APIRouter()

    @router.get("/api/stats/overview")
    async def overview(days: int = Query(14, ge=1, le=90),
                       user_id: str = Depends(current_user)):
        return stats_service.overview(user_id, days=days)

    @router.get("/api/stats/memory")
    async def memory(limit: int = Query(50, ge=1, le=200),
                     user_id: str = Depends(current_user)):
        return stats_service.memory_items(user_id, limit)

    @router.delete("/api/stats/memory/{mem_id}")
    async def delete_memory(mem_id: str, user_id: str = Depends(current_user)):
        if not stats_service.delete_memory(user_id, mem_id):
            raise HTTPException(status_code=404, detail="记忆不存在或无权删除")
        return {"ok": True}

    return router

# app/api/conversations.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user


class _Create(BaseModel):
    title: str | None = None


class _Rename(BaseModel):
    title: str


def make_conversations_router(store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/conversations")
    async def list_conversations(user_id: str = Depends(current_user)):
        return store.list(user_id)

    @router.post("/api/conversations")
    async def create_conversation(body: _Create, user_id: str = Depends(current_user)):
        return {"id": store.create(user_id, body.title or "新对话")}

    @router.patch("/api/conversations/{conv_id}")
    async def rename_conversation(conv_id: str, body: _Rename,
                                  user_id: str = Depends(current_user)):
        title = body.title.strip()
        if not title:
            raise HTTPException(status_code=422, detail="标题不能为空")
        if not store.rename(user_id, conv_id, title):
            raise HTTPException(status_code=404, detail="对话不存在")
        return {"ok": True}

    @router.get("/api/conversations/{conv_id}/messages")
    async def get_messages(conv_id: str, user_id: str = Depends(current_user)):
        if not store.exists(user_id, conv_id):
            raise HTTPException(status_code=404, detail="对话不存在")
        return store.ui_messages(conv_id)

    @router.delete("/api/conversations/{conv_id}")
    async def delete_conversation(conv_id: str, user_id: str = Depends(current_user)):
        store.delete(user_id, conv_id)
        return {"ok": True}

    return router

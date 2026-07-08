# app/api/conversations.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class _Create(BaseModel):
    title: str | None = None


def make_conversations_router(store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/conversations")
    async def list_conversations():
        return store.list()

    @router.post("/api/conversations")
    async def create_conversation(body: _Create):
        return {"id": store.create(body.title or "新对话")}

    @router.get("/api/conversations/{conv_id}/messages")
    async def get_messages(conv_id: str):
        if not store.exists(conv_id):
            raise HTTPException(status_code=404, detail="对话不存在")
        return [{"role": m.role.value, "content": m.content} for m in store.messages(conv_id)]

    @router.delete("/api/conversations/{conv_id}")
    async def delete_conversation(conv_id: str):
        store.delete(conv_id)
        return {"ok": True}

    return router

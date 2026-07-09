# app/api/wrong_answers.py
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth import current_user


class IdsBody(BaseModel):
    ids: list[str]


def make_wrong_answers_router(wrong_store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/wrong-answers")
    async def list_wrong(user_id: str = Depends(current_user)):
        return wrong_store.list(user_id)

    @router.post("/api/wrong-answers/delete")
    async def delete_wrong(body: IdsBody, user_id: str = Depends(current_user)):
        wrong_store.delete_many(user_id, body.ids)
        return {"ok": True}

    @router.delete("/api/wrong-answers/{wid}")
    async def delete_one_wrong(wid: str, user_id: str = Depends(current_user)):
        wrong_store.delete(user_id, wid)
        return {"ok": True}

    return router

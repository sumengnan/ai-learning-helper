# app/api/profile.py
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth import current_user


class ProfileBody(BaseModel):
    identity: str = ""
    goal: str = ""
    explain_prefs: list[str] = Field(default_factory=list)
    tone: str = ""
    notes: str = ""


def make_profile_router(profile_store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/profile")
    async def get_profile(user_id: str = Depends(current_user)):
        return profile_store.get(user_id)

    @router.put("/api/profile")
    async def put_profile(body: ProfileBody, user_id: str = Depends(current_user)):
        return profile_store.upsert(user_id, body.model_dump())

    return router

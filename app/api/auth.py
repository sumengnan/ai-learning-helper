# app/api/auth.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from ..auth import AuthService, UsernameTaken, current_user


class _Credentials(BaseModel):
    username: str
    password: str

    @field_validator("username", "password")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("不能为空")
        return v


def make_auth_router(auth: AuthService) -> APIRouter:
    router = APIRouter()

    @router.post("/api/auth/register")
    async def register(body: _Credentials):
        if len(body.password) < 6:
            raise HTTPException(status_code=422, detail="密码至少 6 位")
        try:
            uid = auth.users.create(body.username, body.password)
        except UsernameTaken:
            raise HTTPException(status_code=400, detail="账号已存在")
        return {"token": auth.issue_token(uid), "user": auth.users.get(uid)}

    @router.post("/api/auth/login")
    async def login(body: _Credentials):
        uid = auth.users.verify(body.username, body.password)
        if uid is None:
            raise HTTPException(status_code=401, detail="账号或密码错误")
        return {"token": auth.issue_token(uid), "user": auth.users.get(uid)}

    @router.get("/api/auth/me")
    async def me(user_id: str = Depends(current_user)):
        user = auth.users.get(user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="用户不存在")
        return user

    return router

# app/api/auth.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from .. import captcha as captcha_mod
from ..auth import AuthService, UsernameTaken, current_user


class _Credentials(BaseModel):
    username: str
    password: str
    captcha_token: str = ""
    captcha_text: str = ""

    @field_validator("username", "password")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("不能为空")
        return v


def make_auth_router(auth: AuthService, *, require_captcha: bool = False) -> APIRouter:
    router = APIRouter()

    def _check_captcha(body: _Credentials) -> None:
        if require_captcha and not auth.verify_captcha(body.captcha_token, body.captcha_text):
            raise HTTPException(status_code=400, detail="验证码错误或已过期")

    @router.get("/api/auth/captcha")
    async def captcha():
        # 公开端点：签发一枚无状态验证码 token + 图片（data URI），前端渲染并回传
        token, code = auth.issue_captcha()
        return {"token": token, "image": captcha_mod.data_uri(captcha_mod.render_svg(code))}

    @router.post("/api/auth/register")
    async def register(body: _Credentials):
        _check_captcha(body)
        if len(body.password) < 6:
            raise HTTPException(status_code=422, detail="密码至少 6 位")
        try:
            uid = auth.users.create(body.username, body.password)
        except UsernameTaken:
            raise HTTPException(status_code=400, detail="账号已存在")
        return {"token": auth.issue_token(uid), "user": auth.users.get(uid)}

    @router.post("/api/auth/login")
    async def login(body: _Credentials):
        _check_captcha(body)
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

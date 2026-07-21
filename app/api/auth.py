# app/api/auth.py
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from .. import captcha as captcha_mod
from ..auth import AuthService, UsernameTaken, current_user

log = logging.getLogger("app.auth")

_MIN_PASSWORD = 6
_MAX_NAME_LEN = 64


class _Credentials(BaseModel):
    username: str
    password: str
    full_name: str = ""
    captcha_token: str = ""
    captcha_text: str = ""

    @field_validator("username", "password")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("不能为空")
        return v


class _ResetPassword(BaseModel):
    """忘记密码：账号 + 姓名核身，直接设新密码。"""
    username: str
    full_name: str
    new_password: str
    captcha_token: str = ""
    captcha_text: str = ""

    @field_validator("username", "full_name", "new_password")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("不能为空")
        return v


class ResetThrottle:
    """按账号计的重置失败节流。

    姓名是个远弱于密码的凭据——可能公开、可猜、长度短。验证码只能挡住脚本连发，挡不住
    有人对着一个已知账号慢慢试姓名。故在验证码之外再加一道：同一账号连续失败到上限后
    锁一段时间，把「在线猜姓名」的成本从「试几百次」抬到「试几百次 × 冷却时长」。

    按账号而非按 IP 计：换 IP 是攻击者最容易做的事，而这里要保护的正是特定账号。
    代价是同账号的真实用户可能被殃及——冷却期不长，且成功重置即清零。
    进程内内存态，重启即清空；单实例部署下够用，多实例要改共享存储。
    """

    def __init__(self, *, max_failures: int = 5, window: int = 900, now=time.time) -> None:
        self._max = max_failures
        self._window = window
        self._now = now
        self._fails: dict[str, list[float]] = {}

    def _recent(self, username: str) -> list[float]:
        cutoff = self._now() - self._window
        hits = [t for t in self._fails.get(username, []) if t > cutoff]
        if hits:
            self._fails[username] = hits
        else:
            self._fails.pop(username, None)
        return hits

    def blocked(self, username: str) -> bool:
        return len(self._recent(username)) >= self._max

    def record_failure(self, username: str) -> None:
        self._recent(username)          # 先剪掉过期的，避免字典无限长
        self._fails.setdefault(username, []).append(self._now())

    def reset(self, username: str) -> None:
        self._fails.pop(username, None)


def make_auth_router(auth: AuthService, *, require_captcha: bool = False,
                     throttle: ResetThrottle | None = None) -> APIRouter:
    router = APIRouter()
    throttle = throttle if throttle is not None else ResetThrottle()

    def _check_captcha(token: str, text: str) -> None:
        if require_captcha and not auth.verify_captcha(token, text):
            raise HTTPException(status_code=400, detail="验证码错误或已过期")

    @router.get("/api/auth/captcha")
    async def captcha():
        # 公开端点：签发一枚无状态验证码 token + 图片（data URI），前端渲染并回传
        token, code = auth.issue_captcha()
        return {"token": token, "image": captcha_mod.data_uri(captcha_mod.render_svg(code))}

    @router.post("/api/auth/register")
    async def register(body: _Credentials):
        _check_captcha(body.captcha_token, body.captcha_text)
        if len(body.password) < _MIN_PASSWORD:
            raise HTTPException(status_code=422, detail=f"密码至少 {_MIN_PASSWORD} 位")
        # 姓名是找回密码的唯一凭据，注册时必填——留空的账号日后无从自助重置。
        name = body.full_name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="请填写姓名")
        if len(name) > _MAX_NAME_LEN:
            raise HTTPException(status_code=422, detail=f"姓名不超过 {_MAX_NAME_LEN} 字")
        try:
            uid = auth.users.create(body.username, body.password, name)
        except UsernameTaken:
            raise HTTPException(status_code=400, detail="账号已存在")
        return {"token": auth.issue_token(uid), "user": auth.users.get(uid)}

    @router.post("/api/auth/login")
    async def login(body: _Credentials):
        _check_captcha(body.captcha_token, body.captcha_text)
        uid = auth.users.verify(body.username, body.password)
        if uid is None:
            raise HTTPException(status_code=401, detail="账号或密码错误")
        return {"token": auth.issue_token(uid), "user": auth.users.get(uid)}

    @router.post("/api/auth/reset-password")
    async def reset_password(body: _ResetPassword):
        username = body.username.strip()
        # 节流先于验证码校验：否则攻击者拿一枚过期验证码就能免费探测账号是否已被锁。
        if throttle.blocked(username):
            raise HTTPException(status_code=429,
                                detail="尝试次数过多，请稍后再试")
        _check_captcha(body.captcha_token, body.captcha_text)
        if len(body.new_password) < _MIN_PASSWORD:
            raise HTTPException(status_code=422, detail=f"密码至少 {_MIN_PASSWORD} 位")
        uid = auth.users.verify_name(username, body.full_name)
        if uid is None:
            throttle.record_failure(username)
            # 「账号不存在」与「姓名不对」必须给同一句话：分开说等于白送一个账号枚举接口，
            # 攻击者可先批量确认哪些账号真实存在，再集中猜那几个人的姓名。
            log.info("密码重置核身失败 username=%s", username)
            raise HTTPException(status_code=400, detail="账号或姓名不正确")
        auth.users.set_password(uid, body.new_password)
        throttle.reset(username)
        log.info("密码已重置 user_id=%s", uid)
        # 刻意不签发 token：重置后要求重新登录，让用户用新密码走一遍，
        # 也避免「猜对姓名即直接登入」把一次重置变成一次静默的账号接管。
        return {"ok": True}

    @router.get("/api/auth/me")
    async def me(user_id: str = Depends(current_user)):
        user = auth.users.get(user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="用户不存在")
        return user

    return router

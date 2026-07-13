# app/auth.py
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sqlite3
import time
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import Header, HTTPException, Request, Response

from .db import migrate, open_db

_PBKDF2_ROUNDS = 200_000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_password(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt), _PBKDF2_ROUNDS)
    return dk.hex()


class UsernameTaken(Exception):
    """注册用户名已存在。"""


class UserStore:
    def __init__(self, db_path: str | None = None, *,
                 conn: sqlite3.Connection | None = None) -> None:
        if conn is not None:
            self._db = conn
        else:
            self._db = open_db(db_path)
            migrate(self._db)

    def create(self, username: str, password: str) -> str:
        if self.exists_username(username):
            raise UsernameTaken(username)
        uid = uuid4().hex
        salt = _bytes_to_hex_salt()
        self._db.execute(
            "INSERT INTO users(id, username, password_hash, salt, created_at) VALUES (?,?,?,?,?)",
            (uid, username, _hash_password(password, salt), salt, _now_iso()))
        self._db.commit()
        return uid

    def exists_username(self, username: str) -> bool:
        return self._db.execute(
            "SELECT 1 FROM users WHERE username=?", (username,)).fetchone() is not None

    def verify(self, username: str, password: str) -> str | None:
        row = self._db.execute(
            "SELECT id, password_hash, salt FROM users WHERE username=?", (username,)).fetchone()
        if row is None:
            return None
        uid, phash, salt = row
        if hmac.compare_digest(phash, _hash_password(password, salt)):
            return uid
        return None

    def get(self, user_id: str) -> dict | None:
        row = self._db.execute(
            "SELECT id, username FROM users WHERE id=?", (user_id,)).fetchone()
        return {"id": row[0], "username": row[1]} if row else None


def _bytes_to_hex_salt() -> str:
    import secrets
    return secrets.token_hex(16)


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


class AuthService:
    """无状态签名 token：base64url(payload).base64url(hmac_sha256(secret, payload))。

    token 默认 24h 有效；current_user 依赖检测到剩余 < renew_within 秒时，
    经 X-Refresh-Token 响应头下发新 token（滑动续期）。
    """

    def __init__(self, user_store: UserStore, secret: str,
                 ttl: int = 86_400, renew_within: int = 3_600, now=time.time) -> None:
        self.users = user_store
        self._secret = secret.encode("utf-8")
        self._ttl = ttl
        self._renew_within = renew_within
        self._now = now

    def issue_token(self, user_id: str) -> str:
        iat = int(self._now())
        payload = {"uid": user_id, "iat": iat, "exp": iat + self._ttl}
        body = _b64u(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        sig = _b64u(hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest())
        return f"{body}.{sig}"

    def verify_token(self, token: str) -> tuple[str, int] | None:
        """成功返回 (user_id, 剩余秒数)，失败返回 None。"""
        if not token or token.count(".") != 1:
            return None
        body, sig = token.split(".")
        expected = _b64u(hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        try:
            payload = json.loads(_b64u_decode(body))
        except (ValueError, TypeError):
            return None
        exp = payload.get("exp")
        uid = payload.get("uid")
        if not isinstance(exp, int) or not isinstance(uid, str):
            return None
        remaining = exp - int(self._now())
        if remaining <= 0:
            return None
        return uid, remaining

    def renew_within(self) -> int:
        return self._renew_within

    def issue_captcha(self, *, ttl: int = 300) -> tuple[str, str]:
        """签发一枚图形验证码，返回 (无状态 token, 明文 code)。

        code 仅用于渲染图片，不下发给前端；token 交由前端随登录/注册回传校验。
        """
        from . import captcha
        code = captcha.random_code(4)
        return captcha.sign(self._secret, code, ttl=ttl, now=self._now), code

    def verify_captcha(self, token: str, text: str) -> bool:
        """校验用户输入的验证码是否匹配 token（且未过期）。"""
        from . import captcha
        return captcha.verify(self._secret, token, text, now=self._now)


async def current_user(request: Request, response: Response,
                       authorization: str = Header(default="")) -> str:
    """FastAPI 依赖：解析 Bearer token → user_id；快过期则下发 X-Refresh-Token。"""
    auth: AuthService = request.app.state.auth
    token = ""
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    result = auth.verify_token(token)
    if result is None:
        raise HTTPException(status_code=401, detail="未登录或登录已失效")
    uid, remaining = result
    if remaining < auth.renew_within():
        response.headers["X-Refresh-Token"] = auth.issue_token(uid)
    return uid

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


def _norm_name(name: str) -> str:
    """姓名比对前的归一化：去首尾空白、折叠内部空白、大小写无关。

    「张三 」和「张三」、「Li Ming」和「li  ming」是同一个人；让这种差异判失败，
    只会把用户挡在自己账号外面，并不会挡住任何攻击者。
    """
    return " ".join((name or "").split()).casefold()


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

    def create(self, username: str, password: str, full_name: str = "") -> str:
        if self.exists_username(username):
            raise UsernameTaken(username)
        uid = uuid4().hex
        salt = _bytes_to_hex_salt()
        self._db.execute(
            "INSERT INTO users(id, username, password_hash, salt, created_at, full_name)"
            " VALUES (?,?,?,?,?,?)",
            (uid, username, _hash_password(password, salt), salt, _now_iso(),
             _norm_name(full_name) or None))
        self._db.commit()
        return uid

    def verify_name(self, username: str, full_name: str) -> str | None:
        """账号 + 姓名是否对得上；对得上返回 uid，否则 None（供「忘记密码」核身）。

        姓名是个弱得多的凭据（可能公开、可猜），故此处只做身份核对，真正拦住暴力猜测的是
        验证码 + 上层的失败次数节流（见 api/auth.py）。这里三件事必须做到：
        - 归一化后再比：用户重填时的大小写与前后空格差异不该判失败；
        - 常数时间比较：别把「姓名前几个字对了」变成一条可测的旁路。注意要比字节——
          compare_digest 对非 ASCII 的 str 直接抛 TypeError，中文姓名一律走不通；
        - 库里没姓名（老账号，full_name 为 NULL）一律失败——不能让空输入配上空姓名。
          两个空判缺一不可，但只去掉 `not stored` 行为不变（非空输入本就配不上空姓名），
          留着是为了在「日后有人放宽空姓名限制」时仍有一道拦得住的闸。
        """
        row = self._db.execute(
            "SELECT id, full_name FROM users WHERE username=?", (username,)).fetchone()
        if row is None:
            return None
        uid, stored = row
        given = _norm_name(full_name)
        if not stored or not given:
            return None
        return uid if hmac.compare_digest(
            _norm_name(stored).encode("utf-8"), given.encode("utf-8")) else None

    def set_password(self, user_id: str, password: str) -> bool:
        """重置密码。顺带换一枚新 salt：旧 salt 可能已随旧库泄露，重置正是换掉它的时机。"""
        salt = _bytes_to_hex_salt()
        cur = self._db.execute(
            "UPDATE users SET password_hash=?, salt=? WHERE id=?",
            (_hash_password(password, salt), salt, user_id))
        self._db.commit()
        return cur.rowcount > 0

    def set_full_name(self, user_id: str, full_name: str) -> bool:
        """补填/修改姓名。老账号（full_name 为 NULL）靠这个才能用上「忘记密码」。"""
        name = _norm_name(full_name)
        if not name:
            return False
        cur = self._db.execute("UPDATE users SET full_name=? WHERE id=?", (name, user_id))
        self._db.commit()
        return cur.rowcount > 0

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
            "SELECT id, username, full_name FROM users WHERE id=?", (user_id,)).fetchone()
        return {"id": row[0], "username": row[1], "full_name": row[2] or ""} if row else None


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

# app/request_log.py
"""请求入口中间件：把「谁在操作」写进日志上下文，并按请求打一行访问日志。

之前控制台日志只有 conv_id/run_id，多用户同时用时分不清是哪个账号触发的。本中间件在
每个 HTTP 请求最外层解析 Bearer token → user_id → username，塞进日志 contextvar，于是
这次请求（含它派生的后台生成任务，contextvars 随 create_task 复制）里每条日志都带
`[user=xxx ...]` 前缀。

注意用**纯 ASGI 中间件**而不是 `@app.middleware("http")`：后者基于 BaseHTTPMiddleware，
下游 app 跑在另一个 task 里，dispatch 中设的 contextvar 传不到路由函数。
"""
from __future__ import annotations

import logging
import time

from .logging_setup import set_log_context

log = logging.getLogger("app.request")

# 不打访问日志的路径前缀（静态资源/健康检查，噪音大且与用户操作无关）
_QUIET_PREFIXES = ("/assets", "/favicon", "/static")


class UserLogContextMiddleware:
    """解析登录态 → 日志上下文 user=<账号>，并记录一行 `方法 路径 -> 状态码 耗时`。"""

    def __init__(self, app, auth, access_log: bool = True) -> None:
        self.app = app
        self._auth = auth
        self._access_log = access_log
        self._names: dict[str, str] = {}   # user_id -> username，省掉每请求一次查库

    def _username(self, uid: str) -> str:
        name = self._names.get(uid)
        if name is None:
            try:
                row = self._auth.users.get(uid)
            except Exception:              # 查库失败不该影响请求
                row = None
            name = (row or {}).get("username") or uid
            self._names[uid] = name
        return name

    def _resolve(self, scope) -> str:
        """从 Authorization 头解析出账号名；未登录/无效 token 返回空串。"""
        for k, v in scope.get("headers") or ():
            if k == b"authorization":
                token = v.decode("latin-1", "ignore").strip()
                if token[:7].lower() == "bearer ":
                    token = token[7:].strip()
                try:
                    result = self._auth.verify_token(token)
                except Exception:
                    return ""
                return self._username(result[0]) if result else ""
        return ""

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        user = self._resolve(scope)
        status = 0

        async def send_wrapper(message):
            nonlocal status
            if message.get("type") == "http.response.start":
                status = message.get("status", 0)
            await send(message)

        started = time.time()
        with set_log_context(user=user or "匿名"):
            try:
                await self.app(scope, receive, send_wrapper)
            finally:
                if self._access_log and not path.startswith(_QUIET_PREFIXES):
                    log.info("%s %s -> %s %dms", scope.get("method", "?"), path,
                             status or "中断", int((time.time() - started) * 1000))

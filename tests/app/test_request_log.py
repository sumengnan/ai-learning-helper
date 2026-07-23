import asyncio
import logging

from app.logging_setup import _CorrelationFilter, set_log_context
from app.request_log import UserLogContextMiddleware


class _FakeUsers:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, uid):
        self.calls += 1
        return {"id": uid, "username": "alice", "full_name": ""}


class _FakeAuth:
    def __init__(self, valid_token="tok") -> None:
        self.users = _FakeUsers()
        self._valid = valid_token

    def verify_token(self, token):
        return ("u1", 9_999) if token == self._valid else None


def _scope(headers=(), path="/api/chat", method="POST"):
    return {"type": "http", "path": path, "method": method, "headers": list(headers)}


def _corr_seen(auth, scope, mw=None):
    """跑一次中间件，返回下游看到的日志关联串。"""
    seen = {}

    async def app(scope, receive, send):
        rec = logging.LogRecord("n", logging.INFO, "p", 1, "m", (), None)
        _CorrelationFilter().filter(rec)
        seen["corr"] = rec.corr
        await send({"type": "http.response.start", "status": 200})

    async def send(msg):
        pass

    mw = mw or UserLogContextMiddleware(app, auth, access_log=False)
    mw.app = app
    asyncio.run(mw(scope, None, send))
    return seen["corr"]


def test_logged_in_request_carries_username():
    corr = _corr_seen(_FakeAuth(), _scope([(b"authorization", b"Bearer tok")]))
    assert corr == "user=alice"


def test_anonymous_request_marked():
    assert _corr_seen(_FakeAuth(), _scope()) == "user=匿名"


def test_invalid_token_is_anonymous():
    corr = _corr_seen(_FakeAuth(), _scope([(b"authorization", b"Bearer bad")]))
    assert corr == "user=匿名"


def test_username_cached_across_requests():
    auth = _FakeAuth()
    mw = UserLogContextMiddleware(None, auth, access_log=False)
    _corr_seen(auth, _scope([(b"authorization", b"Bearer tok")]), mw)
    _corr_seen(auth, _scope([(b"authorization", b"Bearer tok")]), mw)
    assert auth.users.calls == 1        # 第二次走缓存，不再查库


def test_context_restored_after_request():
    auth = _FakeAuth()
    _corr_seen(auth, _scope([(b"authorization", b"Bearer tok")]))
    rec = logging.LogRecord("n", logging.INFO, "p", 1, "m", (), None)
    _CorrelationFilter().filter(rec)
    assert rec.corr == "-"


def test_downstream_context_keeps_user():
    """业务代码后续设 conv/run 时不该冲掉入口设的 user。"""
    seen = {}

    async def app(scope, receive, send):
        with set_log_context(conv_id="c1", run_id="r1"):
            rec = logging.LogRecord("n", logging.INFO, "p", 1, "m", (), None)
            _CorrelationFilter().filter(rec)
            seen["corr"] = rec.corr

    async def send(msg):
        pass

    mw = UserLogContextMiddleware(app, _FakeAuth(), access_log=False)
    asyncio.run(mw(_scope([(b"authorization", b"Bearer tok")]), None, send))
    assert seen["corr"] == "user=alice conv_id=c1 run_id=r1"


def test_access_log_line(caplog):
    auth = _FakeAuth()

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 201})

    async def send(msg):
        pass

    mw = UserLogContextMiddleware(app, auth, access_log=True)
    with caplog.at_level(logging.INFO, logger="app.request"):
        asyncio.run(mw(_scope([(b"authorization", b"Bearer tok")]), None, send))
    assert any("POST /api/chat -> 201" in r.getMessage() for r in caplog.records)


def test_static_paths_not_access_logged(caplog):
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200})

    async def send(msg):
        pass

    mw = UserLogContextMiddleware(app, _FakeAuth(), access_log=True)
    with caplog.at_level(logging.INFO, logger="app.request"):
        asyncio.run(mw(_scope(path="/assets/index.js", method="GET"), None, send))
    assert not caplog.records


def test_non_http_scope_passes_through():
    called = {}

    async def app(scope, receive, send):
        called["ok"] = True

    async def send(msg):
        pass

    mw = UserLogContextMiddleware(app, _FakeAuth(), access_log=False)
    asyncio.run(mw({"type": "lifespan"}, None, send))
    assert called["ok"]

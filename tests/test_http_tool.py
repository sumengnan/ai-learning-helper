import httpx
import pytest

from harness.tools.base import ToolRegistry, ToolExecutor
from harness.tools.builtins.http_tool import HttpRequestTool
from harness.types import ToolCall


def _factory(handler):
    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


def _public(host):
    return ["93.184.216.34"]


async def test_success_returns_body():
    def handler(req):
        return httpx.Response(200, text="hello world")
    tool = HttpRequestTool([], True, 5.0, 1000, 3, client_factory=_factory(handler), resolve=_public)
    out = await tool.run(tool.Params(url="http://example.com/"))
    assert "200" in out and "hello world" in out


async def test_ssrf_blocked_is_error():
    def handler(req):
        return httpx.Response(200, text="secret")
    tool = HttpRequestTool([], True, 5.0, 1000, 3,
                           client_factory=_factory(handler), resolve=lambda h: ["127.0.0.1"])
    reg = ToolRegistry(); reg.register(tool)
    ex = ToolExecutor(reg)
    r = await ex.execute(ToolCall(id="c1", name="http_request",
                                  arguments={"url": "http://internal/"}))
    assert r.is_error is True


async def test_response_truncated():
    def handler(req):
        return httpx.Response(200, text="A" * 5000)
    tool = HttpRequestTool([], True, 5.0, 100, 3, client_factory=_factory(handler), resolve=_public)
    out = await tool.run(tool.Params(url="http://example.com/"))
    assert "…(已截断)" in out


async def test_redirect_to_internal_blocked():
    def handler(req):
        if req.url.host == "example.com":
            return httpx.Response(302, headers={"location": "http://internal/"})
        return httpx.Response(200, text="internal")
    # example.com 公网、internal 解析到内网
    def resolve(host):
        return ["93.184.216.34"] if host == "example.com" else ["127.0.0.1"]
    tool = HttpRequestTool([], True, 5.0, 1000, 3, client_factory=_factory(handler), resolve=resolve)
    reg = ToolRegistry(); reg.register(tool)
    ex = ToolExecutor(reg)
    r = await ex.execute(ToolCall(id="c1", name="http_request",
                                  arguments={"url": "http://example.com/"}))
    assert r.is_error is True   # 第二跳内网被策略拦截

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


_ARTICLE = (
    "<html><head><title>光合作用</title></head><body>"
    "<nav>首页 关于 登录 注册</nav>"
    "<article><h1>光合作用的原理</h1>"
    "<p>光合作用是绿色植物利用光能，把二氧化碳和水转化为储存能量的有机物，并释放氧气的过程。"
    "这一过程主要发生在叶绿体中，是地球上几乎所有生命能量的最终来源，对维持大气中氧气与二氧化碳的平衡至关重要。</p>"
    "</article><footer>版权所有 © 2026</footer></body></html>"
)


async def test_html_returns_parsed_title_and_text():
    def handler(req):
        return httpx.Response(200, text=_ARTICLE, headers={"content-type": "text/html; charset=utf-8"})
    tool = HttpRequestTool([], True, 5.0, 5_000_000, 3, client_factory=_factory(handler), resolve=_public)
    out = await tool.run(tool.Params(url="http://example.com/"))
    assert "标题：光合作用" in out
    assert "光合作用是绿色植物" in out
    assert "<html>" not in out and "<nav>" not in out   # 不再倒原始标签墙
    assert "登录 注册" not in out                          # 样板被去掉


async def test_raw_flag_returns_original_html():
    def handler(req):
        return httpx.Response(200, text=_ARTICLE, headers={"content-type": "text/html"})
    tool = HttpRequestTool([], True, 5.0, 5_000_000, 3, client_factory=_factory(handler), resolve=_public)
    out = await tool.run(tool.Params(url="http://example.com/", raw=True))
    assert "<nav>" in out                                  # raw=true 原样返回


async def test_json_passthrough():
    def handler(req):
        return httpx.Response(200, text='{"ok": true, "n": 1}',
                              headers={"content-type": "application/json"})
    tool = HttpRequestTool([], True, 5.0, 5_000_000, 3, client_factory=_factory(handler), resolve=_public)
    out = await tool.run(tool.Params(url="http://example.com/api"))
    assert '{"ok": true, "n": 1}' in out
    assert "标题：" not in out


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


# ---------- 浏览器自动兜底 ----------

from harness.tools.builtins.http_tool import looks_blocked  # noqa: E402


def test_looks_blocked_heuristics():
    assert looks_blocked(403, "") is True
    assert looks_blocked(429, "") is True
    assert looks_blocked(200, "Just a moment... Cloudflare") is True
    assert looks_blocked(200, "请开启JavaScript后重试") is True
    assert looks_blocked(200, "正常正文内容") is False


def _fallback(record, text="浏览器抓到的正文", fail=False):
    async def fn(url):
        record.append(url)
        if fail:
            raise RuntimeError("ModuleNotFoundError: No module named 'playwright'")
        return text
    return fn


async def test_fallback_on_network_error():
    def handler(req):
        raise httpx.ConnectError("boom")
    calls = []
    tool = HttpRequestTool([], True, 5.0, 1000, 3, client_factory=_factory(handler),
                           resolve=_public, browser_fallback=_fallback(calls))
    out = await tool.run(tool.Params(url="http://example.com/"))
    assert calls == ["http://example.com/"]
    assert "已自动改用浏览器抓取" in out and "浏览器抓到的正文" in out


async def test_fallback_on_blocked_status():
    def handler(req):
        return httpx.Response(403, text="Access Denied")
    calls = []
    tool = HttpRequestTool([], True, 5.0, 2000, 3, client_factory=_factory(handler),
                           resolve=_public, browser_fallback=_fallback(calls))
    out = await tool.run(tool.Params(url="http://example.com/"))
    assert calls and "浏览器抓到的正文" in out


async def test_policy_error_does_not_fallback():
    def handler(req):
        return httpx.Response(200, text="secret")
    calls = []
    tool = HttpRequestTool([], True, 5.0, 1000, 3, client_factory=_factory(handler),
                           resolve=lambda h: ["127.0.0.1"], browser_fallback=_fallback(calls))
    reg = ToolRegistry(); reg.register(tool)
    r = await ToolExecutor(reg).execute(
        ToolCall(id="c1", name="http_request", arguments={"url": "http://internal/"}))
    assert r.is_error is True
    assert calls == []          # 安全拦截不触发浏览器兜底


async def test_blocked_but_fallback_fails_returns_http_result():
    def handler(req):
        return httpx.Response(403, text="Access Denied")
    calls = []
    tool = HttpRequestTool([], True, 5.0, 2000, 3, client_factory=_factory(handler),
                           resolve=_public, browser_fallback=_fallback(calls, fail=True))
    out = await tool.run(tool.Params(url="http://example.com/"))
    assert calls                # 尝试过兜底
    assert "403" in out and "Access Denied" in out   # 兜底失败 → 退回 http 原结果


async def test_normal_page_no_fallback():
    def handler(req):
        return httpx.Response(200, text="<html><body>正文很充实的内容</body></html>")
    calls = []
    tool = HttpRequestTool([], True, 5.0, 2000, 3, client_factory=_factory(handler),
                           resolve=_public, browser_fallback=_fallback(calls))
    out = await tool.run(tool.Params(url="http://example.com/"))
    assert calls == []          # 正常页面不触发兜底
    assert "200" in out


# ---- User-Agent：空 UA 会被不少站点直接 403，故默认必须带一个 ----

_UA = "Mozilla/5.0 (compatible; AI-Learning-Helper/1.0; +harness)"


async def test_default_user_agent_is_sent():
    seen = {}

    def handler(req):
        seen["ua"] = req.headers.get("user-agent")
        return httpx.Response(200, text="ok")
    tool = HttpRequestTool([], True, 5.0, 1000, 3, client_factory=_factory(handler),
                           resolve=_public, user_agent=_UA)
    await tool.run(tool.Params(url="http://example.com/"))
    assert seen["ua"] == _UA


async def test_explicit_user_agent_wins_over_default():
    seen = {}

    def handler(req):
        seen["ua"] = req.headers.get("user-agent")
        return httpx.Response(200, text="ok")
    tool = HttpRequestTool([], True, 5.0, 1000, 3, client_factory=_factory(handler),
                           resolve=_public, user_agent=_UA)
    await tool.run(tool.Params(url="http://example.com/",
                               headers={"user-agent": "MyBot/9"}))   # 大小写不敏感
    assert seen["ua"] == "MyBot/9"


async def test_other_headers_survive_ua_merge():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        seen["ua"] = req.headers.get("user-agent")
        return httpx.Response(200, text="ok")
    tool = HttpRequestTool([], True, 5.0, 1000, 3, client_factory=_factory(handler),
                           resolve=_public, user_agent=_UA)
    await tool.run(tool.Params(url="http://example.com/", headers={"Authorization": "Bearer t"}))
    assert seen["auth"] == "Bearer t" and seen["ua"] == _UA


async def test_ua_merge_does_not_mutate_caller_headers():
    def handler(req):
        return httpx.Response(200, text="ok")
    tool = HttpRequestTool([], True, 5.0, 1000, 3, client_factory=_factory(handler),
                           resolve=_public, user_agent=_UA)
    headers = {"X-K": "v"}
    await tool.run(tool.Params(url="http://example.com/", headers=headers))
    assert headers == {"X-K": "v"}          # 调用方传进来的 dict 不该被就地改写


async def test_empty_user_agent_config_sends_none():
    # 显式设空 → 退回旧行为（不发 UA），逃生口
    seen = {}

    def handler(req):
        seen["ua"] = req.headers.get("user-agent")
        return httpx.Response(200, text="ok")
    tool = HttpRequestTool([], True, 5.0, 1000, 3, client_factory=_factory(handler),
                           resolve=_public, user_agent="")
    await tool.run(tool.Params(url="http://example.com/"))
    assert seen["ua"] is None or "python-httpx" in seen["ua"].lower()

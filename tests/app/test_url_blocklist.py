"""抓取失败网址登记：分级 TTL 分类、抓前短路、抓后登记。"""
import pytest
from pydantic import BaseModel

from app.url_blocklist import (
    TTL_EMPTY, TTL_FORBIDDEN, TTL_GONE, TTL_TRANSIENT, UrlBlockedError, UrlBlockStore,
    classify, domain_of, guard_fetch_tool, norm_url)
from harness.net.policy import PolicyError
from harness.tools.base import Tool, ToolError


# ---- 规范化 ----

def test_norm_url_ignores_www_case_and_fragment():
    a = norm_url("HTTPS://WWW.Example.com/a/b/#frag")
    assert a == norm_url("https://example.com/a/b") == "https://example.com/a/b"


def test_norm_url_keeps_query():
    # 不同 query 是不同页面，不能混为一谈
    assert norm_url("https://x.com/s?q=1") != norm_url("https://x.com/s?q=2")


def test_domain_of_strips_www():
    assert domain_of("https://www.example.com/a") == "example.com"


# ---- 分类：按失败类型分级 ----

def test_classify_404_blocks_url_for_30_days():
    key, scope, reason, status, ttl = classify("https://x.com/gone", "HTTP 404\n没有", None)
    assert scope == "url" and status == 404 and ttl == TTL_GONE
    assert key == "https://x.com/gone" and "页面不存在" in reason


def test_classify_403_blocks_whole_domain_for_7_days():
    # 反爬通常是整站行为，只拉黑单页等于没拉
    key, scope, reason, status, ttl = classify("https://x.com/a", "HTTP 403\n拒绝", None)
    assert scope == "domain" and key == "x.com" and ttl == TTL_FORBIDDEN


@pytest.mark.parametrize("status", [500, 502, 503, 429])
def test_classify_transient_blocks_url_for_1_hour(status):
    key, scope, _, st, ttl = classify("https://x.com/a", f"HTTP {status}\n炸了", None)
    assert scope == "url" and st == status and ttl == TTL_TRANSIENT


def test_classify_exception_is_transient():
    _, scope, reason, status, ttl = classify("https://x.com/a", None, TimeoutError("超时"))
    assert scope == "url" and status is None and ttl == TTL_TRANSIENT
    assert "TimeoutError" in reason


def test_classify_policy_error_not_blocked():
    # 安全策略拦截是我们自己的规则，不是网站抓不到；重查不花钱，别占登记表
    assert classify("http://127.0.0.1/x", None, PolicyError("内网")) is None


def test_classify_empty_content_blocks_url_for_7_days():
    _, scope, _, _, ttl = classify("https://x.com/a", "HTTP 200\n标题：X\n\n（无可提取正文）", None)
    assert scope == "url" and ttl == TTL_EMPTY


def test_classify_error_page_with_200_is_blocked():
    # CDN 拦截页常以 200 返回，状态码看不出来，只能靠正文特征
    hit = classify("https://x.com/a", "HTTP 200\nThe request could not be satisfied", None)
    assert hit is not None and hit[4] == TTL_EMPTY


def test_classify_success_is_not_blocked():
    assert classify("https://x.com/a", "HTTP 200\n标题：X\n\n正经内容", None) is None


def test_classify_browser_fallback_success_is_not_blocked():
    # 403 被浏览器兜底救回来了 → 这次抓取是成功的，不能因为原始状态码就拉黑整域
    text = "（HTTP 403 疑似防抓或需 JS 渲染，已自动改用浏览器抓取）\n\n真正的正文内容"
    assert classify("https://x.com/a", text, None) is None


def test_classify_browser_fallback_returning_empty_is_blocked():
    # 兜底跑了但也没拿到正文 → 仍算失败
    text = "（HTTP 403 疑似防抓，已自动改用浏览器抓取）\n\n（页面无可提取正文）"
    hit = classify("https://x.com/a", text, None)
    assert hit is not None and hit[4] == TTL_EMPTY


# ---- 存储 ----

def _store():
    return UrlBlockStore(":memory:")


def test_check_hits_url_and_expires():
    s = _store()
    s.block("https://x.com/a", "url", "HTTP 404", 404, ttl_seconds=60)
    assert s.check("https://x.com/a")["reason"] == "HTTP 404"
    assert s.check("https://www.X.com/a/") is not None      # 规范化后同一条
    assert s.check("https://x.com/other") is None


def test_domain_block_covers_all_paths():
    s = _store()
    s.block("x.com", "domain", "HTTP 403", 403, ttl_seconds=60)
    assert s.check("https://x.com/any/path") is not None
    assert s.check("https://www.x.com/other?q=1") is not None
    assert s.check("https://y.com/a") is None


def test_expired_entry_does_not_block():
    s = _store()
    s.block("https://x.com/a", "url", "临时炸了", 503, ttl_seconds=-1)   # 已过期
    assert s.check("https://x.com/a") is None
    assert s.prune() == 1


def test_reblock_extends_and_counts_hits():
    s = _store()
    s.block("https://x.com/a", "url", "第一次", 503, ttl_seconds=60)
    s.block("https://x.com/a", "url", "第二次", 404, ttl_seconds=60)
    rec = s.check("https://x.com/a")
    assert rec["hits"] == 2 and rec["reason"] == "第二次" and rec["status"] == 404


def test_url_scope_preferred_over_domain_in_check():
    # 两条都命中时优先返回更具体的 url 条目，理由才准确
    s = _store()
    s.block("x.com", "domain", "整域反爬", 403, ttl_seconds=60)
    s.block("https://x.com/a", "url", "这页没了", 404, ttl_seconds=60)
    assert s.check("https://x.com/a")["reason"] == "这页没了"
    assert s.check("https://x.com/b")["reason"] == "整域反爬"


# ---- guard 包装 ----

class _FakeFetch(Tool):
    name = "http_request"
    description = "抓"

    class Params(BaseModel):
        url: str

    def __init__(self, result=None, exc=None):
        self._result, self._exc = result, exc
        self.calls = []

    async def run(self, params):
        self.calls.append(params.url)
        if self._exc:
            raise self._exc
        return self._result


async def test_guard_records_failure_then_short_circuits_next_call():
    s = _store()
    inner = _FakeFetch(result="HTTP 404\n没了")
    tool = guard_fetch_tool(inner, s)

    out = await tool.run(tool.Params(url="https://x.com/gone"))
    assert out == "HTTP 404\n没了"            # 首次照常返回（不改变本次结果）
    assert s.check("https://x.com/gone") is not None

    with pytest.raises(ToolError) as ei:      # 下次直接短路
        await tool.run(tool.Params(url="https://x.com/gone"))
    assert "请改用其它网址或来源" in str(ei.value)
    assert inner.calls == ["https://x.com/gone"]   # 没有再发第二次请求


async def test_guard_short_circuits_by_domain_after_403():
    s = _store()
    inner = _FakeFetch(result="HTTP 403\n拒绝")
    tool = guard_fetch_tool(inner, s)
    await tool.run(tool.Params(url="https://x.com/a"))

    with pytest.raises(ToolError):            # 同域另一个页面也跳过
        await tool.run(tool.Params(url="https://x.com/totally-other"))
    assert inner.calls == ["https://x.com/a"]


async def test_guard_records_exception_and_reraises():
    s = _store()
    tool = guard_fetch_tool(_FakeFetch(exc=TimeoutError("超时")), s)
    with pytest.raises(TimeoutError):         # 原异常照常抛出，不吞
        await tool.run(tool.Params(url="https://x.com/slow"))
    assert s.check("https://x.com/slow") is not None


async def test_guard_does_not_block_success():
    s = _store()
    inner = _FakeFetch(result="HTTP 200\n标题：好\n\n正文")
    tool = guard_fetch_tool(inner, s)
    for _ in range(3):
        await tool.run(tool.Params(url="https://x.com/ok"))
    assert len(inner.calls) == 3 and s.check("https://x.com/ok") is None


async def test_guard_survives_store_failure():
    # 登记表故障不该阻断正常抓取
    class _Broken:
        def check(self, url): raise RuntimeError("库炸了")
        def block(self, *a, **k): raise RuntimeError("库炸了")

    tool = guard_fetch_tool(_FakeFetch(result="HTTP 404\n没了"), _Broken())
    assert await tool.run(tool.Params(url="https://x.com/a")) == "HTTP 404\n没了"


async def test_blocked_url_raises_distinguishable_error():
    # facts 核查要能区分「已知坏链」和「基建抖了一下」——前者是证据，后者才放行
    s = _store()
    s.block("https://x.com/a", "url", "HTTP 404（页面不存在）", 404, ttl_seconds=60)
    tool = guard_fetch_tool(_FakeFetch(result="不该被调用"), s)
    with pytest.raises(UrlBlockedError) as ei:
        await tool.run(tool.Params(url="https://x.com/a"))
    assert ei.value.record["reason"] == "HTTP 404（页面不存在）"
    assert isinstance(ei.value, ToolError)          # 仍是 ToolError → is_error=True


def test_guard_leaves_non_fetch_tools_alone():
    class _Other(Tool):
        name = "calculator"
        description = "算"

        class Params(BaseModel):
            x: int

        async def run(self, params):
            return "2"

    t = _Other()
    assert guard_fetch_tool(t, _store()) is t          # 原样返回，不包装
    assert guard_fetch_tool(_FakeFetch(), None) is not None   # store 为 None → 直通

"""占位域名拦截不应进抓取失败登记表。

现有回归 test_placeholder_url_blocked_before_request 传的是 store=None，
没覆盖到带登记表的组合；生产路径传的是真 store，会触发误登记。
"""
import pytest
from pydantic import BaseModel

from app.url_blocklist import UrlBlockStore, UrlBlockedError, guard_fetch_tool
from harness.tools.base import Tool, ToolError


class _CountingFetch(Tool):
    name = "http_request"
    description = "d"

    class Params(BaseModel):
        url: str

    def __init__(self):
        self.called = 0

    async def run(self, p):
        self.called += 1
        return "HTTP 200\n正文"


async def test_placeholder_block_not_recorded_with_store():
    """占位域名拦截不进抓取失败登记表，第二次撞同一网址仍收到占位守卫原文。"""
    store = UrlBlockStore(":memory:")
    inner = _CountingFetch()
    tool = guard_fetch_tool(inner, store)

    url = "https://api.example.com/ai-trends"

    # 第一次：占位守卫拦截，不应登记
    with pytest.raises(ToolError) as e1:
        await tool.run(tool.Params(url=url))
    assert inner.called == 0, "请求不该发出去"
    assert "未发起请求" in str(e1.value)
    assert "联网搜索工具" in str(e1.value)
    assert not isinstance(e1.value, UrlBlockedError), (
        "占位拦截不应被包装成 UrlBlockedError")

    # 登记表应为空——占位域名拦截不是抓取失败
    assert store.list_active() == [], (
        "占位域名拦截不应进入抓取失败登记表")

    # 第二次：仍应收到占位守卫原文，而不是"近期抓取失败过 / 换一个站点抓"
    with pytest.raises(ToolError) as e2:
        await tool.run(tool.Params(url=url))
    assert inner.called == 0
    assert "未发起请求" in str(e2.value)
    assert "联网搜索工具" in str(e2.value)
    assert not isinstance(e2.value, UrlBlockedError), (
        "第二次拦截仍应是占位守卫原文，不应被登记层短路")

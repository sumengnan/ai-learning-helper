import pytest

from harness.browser.fake import FakeBrowser
from harness.tools.base import ToolRegistry, ToolExecutor
from harness.tools.builtins.browse_tool import BrowseTool
from harness.types import ToolCall

ARTICLE = (
    "<html><head><title>标题T</title></head><body>"
    "<header><nav>首页 关于 联系我们 登录 注册</nav></header>"
    "<article><p>这是一段足够长的正文内容，用于验证浏览器工具能够正确渲染并提取页面主体文字，"
    "而不是把导航栏和页脚一起塞进结果里，从而保证喂给知识库的内容是干净的。这段内容需要再长一些，"
    "以便让提取算法能够明显区分正文段落与周围的导航、页脚等样板内容，从而提升提取的稳定性和准确性。</p></article>"
    "<footer>版权所有 2026 隐私政策 网站地图</footer></body></html>"
)


def _tool(pages, resolve):
    fb = FakeBrowser(pages)
    return BrowseTool(fb, allowed_domains=[], block_private=True, timeout=5,
                      wait_until="load", max_chars=8000, resolve=resolve)


async def test_browse_returns_extracted_text():
    tool = _tool({"http://example.com/a": ("标题T", ARTICLE)},
                 resolve=lambda h: ["93.184.216.34"])
    out = await tool.run(tool.Params(url="http://example.com/a"))
    assert "标题T" in out
    assert "足够长的正文内容" in out
    assert "登录 注册" not in out


async def test_browse_ssrf_is_error():
    tool = _tool({}, resolve=lambda h: ["127.0.0.1"])
    reg = ToolRegistry(); reg.register(tool)
    ex = ToolExecutor(reg)
    r = await ex.execute(ToolCall(id="c1", name="browse",
                                  arguments={"url": "http://internal/"}))
    assert r.is_error is True


async def test_browse_metadata_ip_literal_blocked():
    tool = _tool({}, resolve=None)   # 用真实 default_resolve；IP 字面量不走网络
    reg = ToolRegistry(); reg.register(tool)
    ex = ToolExecutor(reg)
    r = await ex.execute(ToolCall(id="c1", name="browse",
                                  arguments={"url": "http://169.254.169.254/"}))
    assert r.is_error is True


async def test_browse_empty_content_message():
    tool = _tool({"http://example.com/e": ("空页", "<html><body></body></html>")},
                 resolve=lambda h: ["93.184.216.34"])
    out = await tool.run(tool.Params(url="http://example.com/e"))
    assert "无可提取正文" in out

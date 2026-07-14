from types import SimpleNamespace

import pytest

from harness.net.policy import PolicyError
from harness.tools.builtins.sandbox_http_tool import SandboxedHttpRequestTool


class FakeSandbox:
    """记录 exec 调用并按序返回预置输出（DNS 解析输出 / curl -i 输出）。"""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def exec(self, cmd, timeout, *, quiet=False):
        self.calls.append(cmd)
        return SimpleNamespace(stdout=self.outputs.pop(0), stderr="",
                               exit_code=0, timed_out=False)


async def test_sandboxed_http_get_runs_curl_in_sandbox():
    sb = FakeSandbox(["HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nhello world"])
    tool = SandboxedHttpRequestTool(sb, [], block_private=False)
    out = await tool.run(tool.Params(url="https://example.com/"))
    assert out == "HTTP 200\nhello world"
    assert sb.calls[0][0] == "curl"                 # block_private 关：不解析，直接 curl
    assert "https://example.com/" in sb.calls[0]


_ARTICLE = (
    "<html><head><title>光合作用</title></head><body>"
    "<nav>首页 登录 注册</nav>"
    "<article><h1>光合作用的原理</h1>"
    "<p>光合作用是绿色植物利用光能，把二氧化碳和水转化为储存能量的有机物，并释放氧气的过程。"
    "这一过程主要发生在叶绿体中，是地球上几乎所有生命能量的最终来源，对维持大气与二氧化碳平衡至关重要。</p>"
    "</article><footer>版权所有 © 2026</footer></body></html>"
)


async def test_sandboxed_html_returns_parsed_title_and_text():
    sb = FakeSandbox([f"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n{_ARTICLE}"])
    tool = SandboxedHttpRequestTool(sb, [], block_private=False)
    out = await tool.run(tool.Params(url="https://example.com/"))
    assert "标题：光合作用" in out
    assert "光合作用是绿色植物" in out
    assert "<nav>" not in out


async def test_sandboxed_html_raw_flag_returns_original():
    sb = FakeSandbox([f"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n{_ARTICLE}"])
    tool = SandboxedHttpRequestTool(sb, [], block_private=False)
    out = await tool.run(tool.Params(url="https://example.com/", raw=True))
    assert "<nav>" in out


async def test_sandboxed_json_passthrough():
    sb = FakeSandbox(['HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{"ok": true}'])
    tool = SandboxedHttpRequestTool(sb, [], block_private=False)
    out = await tool.run(tool.Params(url="https://example.com/api"))
    assert out == 'HTTP 200\n{"ok": true}'


async def test_sandboxed_http_follows_redirect():
    sb = FakeSandbox([
        "HTTP/1.1 302 Found\r\nLocation: https://example.com/final\r\n\r\n",
        "HTTP/1.1 200 OK\r\n\r\ndone",
    ])
    tool = SandboxedHttpRequestTool(sb, [], block_private=False)
    out = await tool.run(tool.Params(url="https://example.com/start"))
    assert out == "HTTP 200\ndone"
    assert len(sb.calls) == 2
    assert "https://example.com/final" in sb.calls[1]


async def test_dns_resolved_in_sandbox_then_validated_and_pinned():
    # 执行在沙箱：先在容器内解析出公网 IP；决策在宿主：校验通过后把 curl 钉到该 IP
    sb = FakeSandbox(["93.184.216.34", "HTTP/1.1 200 OK\r\n\r\nhi"])
    tool = SandboxedHttpRequestTool(sb, [], block_private=True)
    out = await tool.run(tool.Params(url="https://example.com/"))
    assert out == "HTTP 200\nhi"
    assert sb.calls[0][0] == "getent"              # DNS 解析在沙箱内（getent）
    assert sb.calls[0][-1] == "example.com"
    assert sb.calls[1][0] == "curl"
    assert "--resolve" in sb.calls[1]
    assert "example.com:443:93.184.216.34" in sb.calls[1]


async def test_private_ip_resolved_in_sandbox_blocked_on_host():
    # 容器内解析出内网 IP → 宿主 check_url 拦截 → curl 不执行（决策在宿主）
    sb = FakeSandbox(["10.0.0.1"])
    tool = SandboxedHttpRequestTool(sb, [], block_private=True)
    with pytest.raises(PolicyError):
        await tool.run(tool.Params(url="http://internal/"))
    assert len(sb.calls) == 1                        # 只做了解析，没发 curl
    assert sb.calls[0][0] == "getent"

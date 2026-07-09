from types import SimpleNamespace

import pytest

from harness.net.policy import PolicyError
from harness.tools.builtins.sandbox_http_tool import SandboxedHttpRequestTool


class FakeSandbox:
    """记录 exec 调用并按序返回预置的 curl -i 输出。"""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def exec(self, cmd, timeout):
        self.calls.append(cmd)
        return SimpleNamespace(stdout=self.outputs.pop(0), stderr="",
                               exit_code=0, timed_out=False)


async def test_sandboxed_http_get_runs_curl_in_sandbox():
    sb = FakeSandbox(["HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nhello world"])
    tool = SandboxedHttpRequestTool(sb, [], block_private=False)
    out = await tool.run(tool.Params(url="https://example.com/"))
    assert out == "HTTP 200\nhello world"
    assert sb.calls[0][0] == "curl"                 # 在沙箱内用 curl 执行
    assert "https://example.com/" in sb.calls[0]


async def test_sandboxed_http_follows_redirect_with_policy_recheck():
    sb = FakeSandbox([
        "HTTP/1.1 302 Found\r\nLocation: https://example.com/final\r\n\r\n",
        "HTTP/1.1 200 OK\r\n\r\ndone",
    ])
    tool = SandboxedHttpRequestTool(sb, [], block_private=False)
    out = await tool.run(tool.Params(url="https://example.com/start"))
    assert out == "HTTP 200\ndone"
    assert len(sb.calls) == 2                        # 手动跟随了一次重定向
    assert "https://example.com/final" in sb.calls[1]


async def test_sandboxed_http_blocks_private_before_sandbox():
    sb = FakeSandbox([])
    tool = SandboxedHttpRequestTool(sb, [], block_private=True,
                                    resolve=lambda host: ["10.0.0.1"])
    with pytest.raises(PolicyError):                 # SSRF 在宿主侧先拦，不进沙箱
        await tool.run(tool.Params(url="http://internal/"))
    assert sb.calls == []

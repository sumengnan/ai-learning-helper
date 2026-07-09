# src/harness/tools/builtins/sandbox_http_tool.py
from __future__ import annotations

from urllib.parse import urljoin

from pydantic import BaseModel

from ..base import Tool
from ...net.policy import check_url


def _parse_response(raw: str) -> tuple[int, str | None, str]:
    """解析 curl -i 的输出：返回 (status, location, body)。"""
    sep = "\r\n\r\n" if "\r\n\r\n" in raw else "\n\n"
    head, _, body = raw.partition(sep)
    lines = head.splitlines()
    status = 0
    if lines and lines[0].startswith("HTTP/"):
        parts = lines[0].split()
        if len(parts) >= 2 and parts[1].isdigit():
            status = int(parts[1])
    location = None
    for ln in lines[1:]:
        if ln.lower().startswith("location:"):
            location = ln.split(":", 1)[1].strip()
            break
    return status, location, body


class SandboxedHttpRequestTool(Tool):
    name = "http_request"
    description = ("发起 HTTP(S) 请求抓取网页或调用外部 API（在沙箱容器内用 curl 执行，"
                   "出网来自沙箱）。默认可访问公网，禁止内网地址。")

    class Params(BaseModel):
        url: str
        method: str = "GET"
        headers: dict | None = None
        body: str | None = None

    def __init__(self, sandbox, allowed_domains, block_private: bool = True,
                 timeout: float = 30.0, max_bytes: int = 5_000_000,
                 max_redirects: int = 5, resolve=None) -> None:
        self._sandbox = sandbox
        self._allowed = allowed_domains
        self._block_private = block_private
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._resolve_kw = {"resolve": resolve} if resolve is not None else {}

    async def run(self, params: "SandboxedHttpRequestTool.Params") -> str:
        url = params.url
        for _ in range(self._max_redirects + 1):
            # SSRF 策略仍在宿主侧逐跳校验（PolicyError→is_error）；curl 关闭自动重定向手动跟随
            check_url(url, self._allowed, self._block_private, **self._resolve_kw)
            cmd = ["curl", "-s", "-S", "-i", "--max-time", str(max(1, int(self._timeout))),
                   "-X", params.method]
            for k, v in (params.headers or {}).items():
                cmd += ["-H", f"{k}: {v}"]
            if params.body is not None:
                cmd += ["--data-binary", params.body]
            cmd.append(url)
            res = await self._sandbox.exec(cmd, self._timeout + 5)
            if not res.stdout:
                raise RuntimeError(
                    f"curl 无输出（exit {res.exit_code}）："
                    f"{res.stderr.strip() or '容器内可能缺少 curl，请使用含 curl 的镜像'}")
            status, location, body = _parse_response(res.stdout)
            if 300 <= status < 400 and location:
                url = urljoin(url, location)
                continue
            suffix = "…(已截断)" if len(body) > self._max_bytes else ""
            return f"HTTP {status}\n{body[: self._max_bytes]}{suffix}"
        raise RuntimeError(f"超过最大重定向次数（{self._max_redirects}）")

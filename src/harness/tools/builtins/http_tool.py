# src/harness/tools/builtins/http_tool.py
from __future__ import annotations

import httpx
from pydantic import BaseModel

from ..base import Tool
from ...net.policy import check_url


class HttpRequestTool(Tool):
    name = "http_request"
    description = "发起 HTTP(S) 请求抓取网页或调用外部 API。默认可访问公网，禁止内网地址。"

    class Params(BaseModel):
        url: str
        method: str = "GET"
        headers: dict | None = None
        body: str | None = None

    def __init__(self, allowed_domains, block_private: bool = True, timeout: float = 30.0,
                 max_bytes: int = 5_000_000, max_redirects: int = 5,
                 client_factory=None, resolve=None) -> None:
        self._allowed = allowed_domains
        self._block_private = block_private
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(follow_redirects=False, timeout=timeout))
        self._resolve_kw = {"resolve": resolve} if resolve is not None else {}

    async def run(self, params: "HttpRequestTool.Params") -> str:
        url = params.url
        async with self._client_factory() as client:
            for _ in range(self._max_redirects + 1):
                check_url(url, self._allowed, self._block_private, **self._resolve_kw)  # PolicyError→is_error
                resp = await client.request(params.method, url,
                                            headers=params.headers, content=params.body)
                if resp.is_redirect and "location" in resp.headers:
                    url = str(httpx.URL(url).join(resp.headers["location"]))
                    continue
                body = resp.text
                suffix = "…(已截断)" if len(body) > self._max_bytes else ""
                return f"HTTP {resp.status_code}\n{body[: self._max_bytes]}{suffix}"
        raise RuntimeError(f"超过最大重定向次数（{self._max_redirects}）")

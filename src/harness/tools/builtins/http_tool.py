# src/harness/tools/builtins/http_tool.py
from __future__ import annotations

import httpx
from pydantic import BaseModel

from ..base import Tool
from ...browser.extract import extract_title_and_text
from ...net.policy import check_url


def _looks_like_html(content_type: str, body: str) -> bool:
    """判定响应是否为 HTML：优先 Content-Type，缺失时嗅探正文开头。"""
    if "html" in content_type.lower():
        return True
    if content_type:            # 有明确非 HTML 类型（json/纯文本等）则不嗅探
        return False
    head = body[:2048].lstrip().lower()
    return head.startswith("<!doctype html") or head.startswith("<html") or "<html" in head


def render_http_result(status: int, url: str, content_type: str, body: str,
                       suffix: str, raw: bool) -> str:
    """HTML 响应返回解析后的标题+正文；其余（JSON/纯文本/API）原样透传。"""
    if not raw and _looks_like_html(content_type, body):
        title, text = extract_title_and_text(body)
        head = f"HTTP {status}\n标题：{title}\n最终URL：{url}\n\n"
        return f"{head}{text or '（无可提取正文）'}{suffix}"
    return f"HTTP {status}\n{body}{suffix}"


class HttpRequestTool(Tool):
    name = "http_request"
    description = ("发起 HTTP(S) 请求抓取网页或调用外部 API。默认可访问公网，禁止内网地址。"
                   "网页默认返回解析后的标题+正文；需要原始 HTML 时传 raw=true。")

    class Params(BaseModel):
        url: str
        method: str = "GET"
        headers: dict | None = None
        body: str | None = None
        raw: bool = False

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
                async with client.stream(params.method, url,
                                         headers=params.headers, content=params.body) as resp:
                    if resp.is_redirect and "location" in resp.headers:
                        url = str(httpx.URL(url).join(resp.headers["location"]))
                        continue
                    chunks, total = [], 0
                    async for b in resp.aiter_bytes():
                        chunks.append(b)
                        total += len(b)
                        if total > self._max_bytes:
                            break
                    body = b"".join(chunks).decode(errors="replace")[: self._max_bytes]
                    suffix = "…(已截断)" if total > self._max_bytes else ""
                    ctype = resp.headers.get("content-type", "")
                    return render_http_result(resp.status_code, url, ctype, body,
                                              suffix, params.raw)
        raise RuntimeError(f"超过最大重定向次数（{self._max_redirects}）")

# src/harness/tools/builtins/browse_tool.py
from __future__ import annotations

from pydantic import BaseModel

from ..base import Tool
from ...browser.base import Browser
from ...browser.extract import extract_main_text
from ...net.policy import check_url
from ._sandbox_util import truncate


class BrowseTool(Tool):
    name = "browse"
    description = "用无头浏览器打开网页（含 JS 动态渲染）并提取正文，适合 http_request 抓不到内容的页面。"

    class Params(BaseModel):
        url: str

    def __init__(self, browser: Browser, allowed_domains, block_private: bool = True,
                 timeout: float = 30.0, wait_until: str = "networkidle",
                 max_chars: int = 8000, resolve=None) -> None:
        self._browser = browser
        self._allowed = allowed_domains
        self._block_private = block_private
        self._timeout = timeout
        self._wait_until = wait_until
        self._max_chars = max_chars
        self._resolve_kw = {"resolve": resolve} if resolve is not None else {}

    async def run(self, params: "BrowseTool.Params") -> str:
        check_url(params.url, self._allowed, self._block_private, **self._resolve_kw)  # PolicyError→is_error
        page = await self._browser.fetch(params.url, self._timeout, self._wait_until)
        text = extract_main_text(page.html)
        if not text.strip():
            return f"（页面无可提取正文）标题：{page.title}"
        return truncate(f"标题：{page.title}\n最终URL：{page.final_url}\n\n{text}", self._max_chars)

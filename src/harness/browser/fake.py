# src/harness/browser/fake.py
from __future__ import annotations

from .base import PageResult


class FakeBrowser:
    """测试用 Browser：返回预设 HTML，不下载 chromium、不联网。"""

    def __init__(self, pages: dict[str, tuple[str, str]]) -> None:
        self._pages = pages   # {url: (title, html)}
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.started = False

    async def fetch(self, url: str, timeout: float, wait_until: str) -> PageResult:
        if url not in self._pages:
            raise RuntimeError(f"FakeBrowser 无预设页面：{url}")
        title, html = self._pages[url]
        return PageResult(final_url=url, title=title, html=html)

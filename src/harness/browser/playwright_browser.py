# src/harness/browser/playwright_browser.py
from __future__ import annotations

from .base import PageResult


class PlaywrightBrowser:
    """本地 chromium headless。每次 fetch 用独立 context/page、无跨页状态。"""

    def __init__(self, headless: bool = True, user_agent: str = "") -> None:
        self._headless = headless
        self._user_agent = user_agent or None
        self._pw = None
        self._browser = None

    async def start(self) -> None:
        if self._browser is not None:
            return
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self._headless)

    async def close(self) -> None:
        try:
            if self._browser is not None:
                await self._browser.close()
        finally:
            self._browser = None
            if self._pw is not None:
                await self._pw.stop()
                self._pw = None

    async def fetch(self, url: str, timeout: float, wait_until: str) -> PageResult:
        await self.start()
        context = await self._browser.new_context(
            accept_downloads=False, user_agent=self._user_agent)
        page = await context.new_page()
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout * 1000)
            html = await page.content()
            title = await page.title()
            final_url = page.url
            return PageResult(final_url=final_url, title=title, html=html)
        finally:
            await context.close()

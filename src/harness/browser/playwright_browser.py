# src/harness/browser/playwright_browser.py
from __future__ import annotations

import asyncio

from .base import PageResult


def friendly_browser_error(exc: Exception) -> Exception:
    """把 Playwright「浏览器内核未安装」的晦涩报错翻译成可操作的中文提示。

    其余异常原样返回。这样 browse 工具在缺内核时给出明确的安装指引，而非
    一长串英文 launch 堆栈。"""
    msg = str(exc)
    if "Executable doesn't exist" in msg or "playwright install" in msg:
        return RuntimeError(
            "浏览器内核未安装：browse 工具需要 Chromium。请在项目环境执行 "
            "`uv run playwright install chromium`（或 `playwright install chromium`）后重试。")
    return exc


class PlaywrightBrowser:
    """本地 chromium headless。每次 fetch 用独立 context/page、无跨页状态。

    安全：传入 url_validator 时，对每一跳导航请求（含重定向）逐跳做 SSRF 校验
    （route 拦截），不合规即 abort 导航（→ 工具 is_error）。子资源请求
    （img/script/xhr 等）不做策略校验——属较低风险，其内容不回传给 agent。
    """

    def __init__(self, headless: bool = True, user_agent: str = "") -> None:
        self._headless = headless
        self._user_agent = user_agent or None
        self._pw = None
        self._browser = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._browser is not None:
                return
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
            try:
                self._browser = await self._pw.chromium.launch(headless=self._headless)
            except Exception as e:   # 缺内核等启动失败：清理并给出可操作提示
                await self._pw.stop()
                self._pw = None
                raise friendly_browser_error(e) from e

    async def close(self) -> None:
        try:
            if self._browser is not None:
                await self._browser.close()
        finally:
            self._browser = None
            if self._pw is not None:
                await self._pw.stop()
                self._pw = None

    async def fetch(self, url: str, timeout: float, wait_until: str,
                    url_validator=None) -> PageResult:
        await self.start()
        context = await self._browser.new_context(
            accept_downloads=False, user_agent=self._user_agent)
        if url_validator is not None:
            async def _route(route):
                req = route.request
                if req.is_navigation_request():
                    try:
                        url_validator(req.url)
                    except Exception:
                        await route.abort()
                        return
                await route.continue_()
            await context.route("**/*", _route)
        page = await context.new_page()
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout * 1000)
            html = await page.content()
            title = await page.title()
            final_url = page.url
            return PageResult(final_url=final_url, title=title, html=html)
        finally:
            await context.close()

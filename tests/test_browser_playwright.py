import os
import pytest

from harness.browser.playwright_browser import PlaywrightBrowser


@pytest.mark.skipif(not os.getenv("HARNESS_BROWSER_IT"),
                    reason="需要已安装 chromium（playwright install chromium）+ 设 HARNESS_BROWSER_IT")
async def test_playwright_fetch_data_url():
    br = PlaywrightBrowser(headless=True)
    await br.start()
    try:
        page = await br.fetch(
            "data:text/html,<title>T</title><p>你好世界内容</p>",
            timeout=15, wait_until="load")
        assert "你好世界内容" in page.html
        assert page.title == "T"
    finally:
        await br.close()

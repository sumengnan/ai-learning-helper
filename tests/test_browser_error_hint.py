# tests/test_browser_error_hint.py
from harness.browser.playwright_browser import friendly_browser_error


def test_missing_executable_gives_actionable_hint():
    raw = Exception(
        "BrowserType.launch: Executable doesn't exist at /path/chrome-headless-shell\n"
        "Looks like Playwright was just installed... Please run: playwright install")
    out = friendly_browser_error(raw)
    assert isinstance(out, RuntimeError)
    assert "playwright install chromium" in str(out)
    assert "浏览器内核未安装" in str(out)


def test_other_errors_pass_through_unchanged():
    raw = ValueError("some other failure")
    assert friendly_browser_error(raw) is raw

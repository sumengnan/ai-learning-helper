# src/harness/browser/factory.py
from __future__ import annotations

from .playwright_browser import PlaywrightBrowser


def build_browser(config, sandbox=None):
    # 有沙箱时在容器内跑无头 Chromium（与 http_request 对称）；否则回退宿主进程内 Playwright
    if sandbox is not None:
        from .sandboxed_browser import SandboxedBrowser
        return SandboxedBrowser(
            sandbox, config.http_allowed_domains, config.http_block_private,
            config.browser_user_agent, config.sandbox_browser_launch_args)
    return PlaywrightBrowser(headless=config.browser_headless,
                             user_agent=config.browser_user_agent)

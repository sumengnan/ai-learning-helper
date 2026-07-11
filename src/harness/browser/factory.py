# src/harness/browser/factory.py
from __future__ import annotations

from .playwright_browser import PlaywrightBrowser


def build_browser(config, sandbox=None, sub_factory=None):
    # 有沙箱时在容器内跑无头 Chromium（与 http_request 对称）；否则回退宿主进程内 Playwright。
    # sub_factory：提供则每次抓取起一次性浏览器子沙箱（专用 playwright 镜像）。
    if sandbox is not None:
        from .sandboxed_browser import SandboxedBrowser
        return SandboxedBrowser(
            sandbox, config.http_allowed_domains, config.http_block_private,
            config.browser_user_agent, config.sandbox_browser_launch_args,
            sub_factory=sub_factory)
    return PlaywrightBrowser(headless=config.browser_headless,
                             user_agent=config.browser_user_agent)

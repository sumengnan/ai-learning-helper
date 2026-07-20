# src/harness/browser/factory.py
from __future__ import annotations

from .playwright_browser import PlaywrightBrowser


def build_browser(config, sandbox=None, sub_factory=None, sub_acquire=None):
    # 有沙箱时在容器内跑无头 Chromium（与 http_request 对称）；否则回退宿主进程内 Playwright。
    # sub_acquire：async ()->(box, cached) 的缓存提供者（浏览器子沙箱按会话缓存复用，优先）。
    # sub_factory：旧式一次性子沙箱工厂（用完即销毁）。
    if sandbox is not None:
        from .sandboxed_browser import SandboxedBrowser
        return SandboxedBrowser(
            sandbox, config.http_allowed_domains, config.http_block_private,
            config.browser_user_agent, config.sandbox_browser_launch_args,
            sub_factory=sub_factory, sub_acquire=sub_acquire)
    return PlaywrightBrowser(headless=config.browser_headless,
                             user_agent=config.browser_user_agent)

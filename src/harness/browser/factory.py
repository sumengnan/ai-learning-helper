# src/harness/browser/factory.py
from __future__ import annotations

from .playwright_browser import PlaywrightBrowser


def build_browser(config):
    return PlaywrightBrowser(headless=config.browser_headless,
                             user_agent=config.browser_user_agent)

# examples/browser_demo.py
"""浏览器工具手动验收：让 agent 抓取一个真实网页并总结。
需要 .env 配好聊天端点，且已 `playwright install chromium`。

运行：uv run python examples/browser_demo.py "抓取 https://example.com 并总结"
"""
from __future__ import annotations

import asyncio
import sys

from harness.config import HarnessConfig
from harness.context.manager import ContextManager
from harness.llm.openai_compat import OpenAICompatibleClient
from harness.loop.agent_loop import AgentLoop
from harness.browser.factory import build_browser
from harness.tools.base import ToolRegistry
from harness.tools.builtins.browse_tool import BrowseTool
from harness.events import TextDelta, ToolStarted, ToolFinished, RunFinished, RunError


async def main(msg: str) -> None:
    cfg = HarnessConfig()
    browser = build_browser(cfg)
    reg = ToolRegistry()
    reg.register(BrowseTool(browser, cfg.http_allowed_domains, cfg.http_block_private,
                            cfg.browser_nav_timeout, cfg.browser_wait_until,
                            cfg.browser_output_max_chars))
    loop = AgentLoop(
        client=OpenAICompatibleClient(cfg), registry=reg,
        context=ContextManager(system_prompt="你可以用 browse 打开网页抓取正文来回答问题。"),
        max_steps=cfg.max_steps, model_name=cfg.model)
    try:
        async for ev in loop.run(msg):
            if isinstance(ev, TextDelta):
                print(ev.text, end="", flush=True)
            elif isinstance(ev, ToolStarted):
                print(f"\n[抓取] {ev.tool_call.arguments}")
            elif isinstance(ev, ToolFinished):
                print(f"[正文] {ev.result.content[:200]}")
            elif isinstance(ev, RunFinished):
                print(f"\n\n[完成] {ev.message.content}")
            elif isinstance(ev, RunError):
                print(f"\n\n[出错] {ev.error}")
    finally:
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "抓取 https://example.com 并总结要点"))

"""订阅事件流逐条打印。需要 .env 配好 HARNESS_API_KEY 等。

运行：uv run python examples/demo.py "帮我算 (12+8)*3"
"""
from __future__ import annotations

import asyncio
import sys

from harness.config import HarnessConfig
from harness.context.manager import ContextManager
from harness.events import (
    RunStarted, StepStarted, TextDelta, ToolCallRequested,
    ToolStarted, ToolFinished, StepFinished, RunFinished, RunError,
)
from harness.llm.openai_compat import OpenAICompatibleClient
from harness.loop.agent_loop import AgentLoop
from harness.tools.base import ToolRegistry
from harness.tools.builtins.calculator import CalculatorTool


async def main(user_message: str) -> None:
    cfg = HarnessConfig()
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    loop = AgentLoop(
        client=OpenAICompatibleClient(cfg),
        registry=registry,
        context=ContextManager(system_prompt=cfg.system_prompt),
        max_steps=cfg.max_steps,
    )

    async for ev in loop.run(user_message):
        if isinstance(ev, RunStarted):
            print(f"\n[run {ev.run_id}]")
        elif isinstance(ev, StepStarted):
            print(f"\n--- step {ev.step} ---")
        elif isinstance(ev, TextDelta):
            print(ev.text, end="", flush=True)
        elif isinstance(ev, ToolCallRequested):
            print(f"\n[请求工具] {[tc.name for tc in ev.tool_calls]}")
        elif isinstance(ev, ToolStarted):
            print(f"[执行] {ev.tool_call.name}({ev.tool_call.arguments})")
        elif isinstance(ev, ToolFinished):
            flag = "ERR" if ev.result.is_error else "OK"
            print(f"[结果:{flag}] {ev.result.content}")
        elif isinstance(ev, StepFinished):
            pass
        elif isinstance(ev, RunFinished):
            print(f"\n\n[完成] {ev.message.content}")
        elif isinstance(ev, RunError):
            print(f"\n\n[出错] {ev.error}")


if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else "帮我算 (12+8)*3"
    asyncio.run(main(msg))

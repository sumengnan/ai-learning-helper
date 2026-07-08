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

    from harness.reliability.retry import RetryingModelClient
    from harness.reliability.budget import BudgetTracker
    from harness.telemetry.tracer import setup_telemetry, get_tracer
    from harness.events import ModelUsage

    setup_telemetry(cfg)  # otel_enabled=False 时 no-op
    base_client = OpenAICompatibleClient(cfg)
    client = RetryingModelClient(base_client, max_retries=cfg.max_retries,
                                 base_delay=cfg.retry_base_delay)
    budget = BudgetTracker(max_tokens=cfg.max_tokens_budget,
                           max_wall_seconds=cfg.max_wall_seconds)
    loop = AgentLoop(
        client=client,
        registry=registry,
        context=ContextManager(system_prompt=cfg.system_prompt),
        max_steps=cfg.max_steps,
        budget=budget,
        tracer=get_tracer(),
        model_name=cfg.model,
        price_map=cfg.price_map,
        tool_result_max_chars=cfg.tool_result_max_chars,
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
        elif isinstance(ev, ModelUsage):
            cost = f"${ev.cost_usd:.4f}" if ev.cost_usd is not None else "n/a"
            print(f"\n[用量] tokens={ev.usage.total_tokens} cost={cost} "
                  f"retries={ev.attempts - 1} latency={ev.latency_ms:.0f}ms")
        elif isinstance(ev, RunError):
            print(f"\n\n[出错] {ev.error}")


if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else "帮我算 (12+8)*3"
    asyncio.run(main(msg))

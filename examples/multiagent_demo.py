# examples/multiagent_demo.py
"""多 Agent 编排手动验收：主 agent 把子任务派给专职子 agent。
需要 .env 配好聊天端点。

运行：uv run python examples/multiagent_demo.py "先算 (12+8)*3，再解释结果"
"""
from __future__ import annotations

import asyncio
import sys

from harness.config import HarnessConfig
from harness.context.manager import ContextManager
from harness.llm.openai_compat import OpenAICompatibleClient
from harness.loop.agent_loop import AgentLoop
from harness.reliability.budget import BudgetTracker
from harness.tools.base import ToolRegistry
from harness.tools.builtins.calculator import CalculatorTool
from harness.orchestration.spec import AgentSpec, AgentRoster
from harness.orchestration.dispatch import DispatchTool
from harness.events import TextDelta, ToolStarted, ToolFinished, RunFinished, RunError


async def main(msg: str) -> None:
    cfg = HarnessConfig()
    client = OpenAICompatibleClient(cfg)
    budget = BudgetTracker(max_tokens=cfg.max_tokens_budget, max_wall_seconds=cfg.max_wall_seconds)

    pool = {"calculator": CalculatorTool()}
    roster = AgentRoster([
        AgentSpec("calculator_agent", "擅长用 calculator 精确计算",
                  "你是计算专家，用 calculator 工具算出结果并简述。", ["calculator"]),
    ])
    dispatch = DispatchTool(roster, pool, client, budget=budget,
                            depth=0, max_depth=cfg.max_dispatch_depth,
                            sub_max_steps=cfg.sub_agent_max_steps,
                            model_name=cfg.model, price_map=cfg.price_map)
    reg = ToolRegistry(); reg.register(dispatch)
    loop = AgentLoop(
        client=client, registry=reg,
        context=ContextManager(system_prompt="你是主控 agent，需要精确计算时用 dispatch 派给 calculator_agent，最后汇总回答。"),
        max_steps=cfg.max_steps, budget=budget, model_name=cfg.model)

    async for ev in loop.run(msg):
        if isinstance(ev, TextDelta):
            print(ev.text, end="", flush=True)
        elif isinstance(ev, ToolStarted):
            print(f"\n[派发/工具] {ev.tool_call.name} {ev.tool_call.arguments}")
        elif isinstance(ev, ToolFinished):
            print(f"[返回] {ev.result.content[:200]}")
        elif isinstance(ev, RunFinished):
            print(f"\n\n[完成] {ev.message.content}")
        elif isinstance(ev, RunError):
            print(f"\n\n[出错] {ev.error}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "算 (12+8)*3 并解释"))

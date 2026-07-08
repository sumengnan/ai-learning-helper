"""真实端点集成测试。需要 HARNESS_API_KEY，无 key 时整文件跳过（不打网络）。

运行前需 .env 配好 HARNESS_API_KEY / HARNESS_BASE_URL / HARNESS_MODEL。
"""
import os

import pytest

from harness.config import HarnessConfig
from harness.context.manager import ContextManager
from harness.events import RunFinished, ToolFinished
from harness.llm.openai_compat import OpenAICompatibleClient
from harness.loop.agent_loop import AgentLoop
from harness.tools.base import ToolRegistry
from harness.tools.builtins.calculator import CalculatorTool

pytestmark = pytest.mark.skipif(
    not os.getenv("HARNESS_API_KEY"),
    reason="需要真实 HARNESS_API_KEY 才能跑真实端点集成测试",
)


async def test_real_endpoint_calculator_flow():
    cfg = HarnessConfig()
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    loop = AgentLoop(
        client=OpenAICompatibleClient(cfg),
        registry=registry,
        context=ContextManager(system_prompt=cfg.system_prompt),
        max_steps=cfg.max_steps,
    )

    events = [ev async for ev in loop.run("帮我算 (12+8)*3")]

    assert any(isinstance(ev, RunFinished) for ev in events)
    tool_finished = [ev for ev in events if isinstance(ev, ToolFinished)]
    assert tool_finished, "期望至少触发一次工具调用"
    assert any(ev.result.is_error is False for ev in tool_finished)

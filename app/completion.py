# app/completion.py
from __future__ import annotations

from harness.context.manager import ContextManager
from harness.events import RunError, RunFinished
from harness.loop.agent_loop import AgentLoop
from harness.tools.base import ToolRegistry


def build_completer(client, model_name: str):
    """返回 async (system_prompt, user_prompt) -> str：跑一轮无工具 AgentLoop，取最终文本。

    复用 harness 的重试/预算/OTel 封装；不给 harness 加任何能力。
    """
    async def complete(system_prompt: str, user_prompt: str) -> str:
        loop = AgentLoop(client=client, registry=ToolRegistry(),
                         context=ContextManager(system_prompt),
                         max_steps=1, model_name=model_name)
        final = ""
        async for ev in loop.run(user_prompt):
            if isinstance(ev, RunFinished):
                final = ev.message.content or ""
            elif isinstance(ev, RunError):
                raise RuntimeError(ev.error)
        return final

    return complete

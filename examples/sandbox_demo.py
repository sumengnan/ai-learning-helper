# examples/sandbox_demo.py
"""沙箱 + HTTP 工具手动验收。默认 LocalSandbox（sandbox_backend=local）。
切远程容器：在 .env 设 HARNESS_SANDBOX_BACKEND=docker + HARNESS_SANDBOX_DOCKER_HOST=ssh://user@host。

运行：uv run python examples/sandbox_demo.py "用 python 算 12 的阶乘"
"""
from __future__ import annotations

import asyncio
import sys

from harness.config import HarnessConfig
from harness.context.manager import ContextManager
from harness.llm.openai_compat import OpenAICompatibleClient
from harness.loop.agent_loop import AgentLoop
from harness.sandbox.factory import build_sandbox
from harness.tools.base import ToolRegistry
from harness.tools.builtins.fs_tools import WriteFileTool, ReadFileTool, ListFilesTool
from harness.tools.builtins.shell_tool import RunShellTool
from harness.tools.builtins.code_tool import RunPythonTool
from harness.tools.builtins.http_tool import HttpRequestTool
from harness.events import TextDelta, ToolStarted, ToolFinished, RunFinished, RunError


async def main(msg: str) -> None:
    cfg = HarnessConfig()
    sb = build_sandbox(cfg)
    reg = ToolRegistry()
    reg.register(WriteFileTool(sb))
    reg.register(ReadFileTool(sb, cfg.sandbox_output_max_chars))
    reg.register(ListFilesTool(sb))
    reg.register(RunShellTool(sb, cfg.sandbox_exec_timeout, cfg.sandbox_output_max_chars))
    reg.register(RunPythonTool(sb, cfg.sandbox_exec_timeout, cfg.sandbox_output_max_chars))
    reg.register(HttpRequestTool(cfg.http_allowed_domains, cfg.http_block_private,
                                 cfg.http_timeout, cfg.http_max_response_bytes,
                                 cfg.http_max_redirects))
    loop = AgentLoop(
        client=OpenAICompatibleClient(cfg), registry=reg,
        context=ContextManager(system_prompt="你可以用沙箱工具执行代码/命令/读写文件、用 http_request 抓取网页。"),
        max_steps=cfg.max_steps, model_name=cfg.model)
    try:
        async for ev in loop.run(msg):
            if isinstance(ev, TextDelta):
                print(ev.text, end="", flush=True)
            elif isinstance(ev, ToolStarted):
                print(f"\n[工具] {ev.tool_call.name} {ev.tool_call.arguments}")
            elif isinstance(ev, ToolFinished):
                print(f"[结果] {ev.result.content[:200]}")
            elif isinstance(ev, RunFinished):
                print(f"\n\n[完成] {ev.message.content}")
            elif isinstance(ev, RunError):
                print(f"\n\n[出错] {ev.error}")
    finally:
        await sb.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "用 python 算 12 的阶乘"))

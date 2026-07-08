# src/harness/tools/builtins/shell_tool.py
from __future__ import annotations

from pydantic import BaseModel

from ..base import Tool
from ...sandbox.base import Sandbox
from ._sandbox_util import format_exec


class RunShellTool(Tool):
    name = "run_shell"
    description = "在沙箱内执行 shell 命令，返回 stdout/stderr/退出码。"

    class Params(BaseModel):
        command: str

    def __init__(self, sandbox: Sandbox, timeout: float = 30.0, max_chars: int = 8000) -> None:
        self._sandbox = sandbox
        self._timeout = timeout
        self._max_chars = max_chars

    async def run(self, params: "RunShellTool.Params") -> str:
        res = await self._sandbox.exec(["sh", "-c", params.command], self._timeout)
        return format_exec(res, self._max_chars)

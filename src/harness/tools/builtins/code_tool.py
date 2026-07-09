# src/harness/tools/builtins/code_tool.py
from __future__ import annotations

from pydantic import BaseModel

from ..base import Tool, ToolError
from ...sandbox.base import Sandbox
from ._sandbox_util import format_exec


class RunPythonTool(Tool):
    name = "run_python"
    description = "在沙箱内执行 Python 代码，返回输出。"

    class Params(BaseModel):
        code: str

    def __init__(self, sandbox: Sandbox, timeout: float = 30.0,
                 max_chars: int = 8000, python_cmd: str = "python3") -> None:
        self._sandbox = sandbox
        self._timeout = timeout
        self._max_chars = max_chars
        self._python_cmd = python_cmd

    async def run(self, params: "RunPythonTool.Params") -> str:
        await self._sandbox.write_file("_run.py", params.code)   # SandboxError→is_error
        res = await self._sandbox.exec([self._python_cmd, "_run.py"], self._timeout)
        out = format_exec(res, self._max_chars)
        if res.exit_code != 0 or res.timed_out:   # 非零退出/超时 → 标记失败
            raise ToolError(out)
        return out

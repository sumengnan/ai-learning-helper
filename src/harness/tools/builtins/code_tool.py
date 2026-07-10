# src/harness/tools/builtins/code_tool.py
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from ..base import Tool, ToolError
from ...sandbox.base import Sandbox
from ._sandbox_util import format_exec


@dataclass
class LangSpec:
    language: str            # 路由到语言容器的 key（对应 config.sandbox_images 的键）
    filename: str            # 源码写入的文件名
    argv: list[str] | None   # 直接执行的命令；None 表示用 shell 编译+运行
    shell: str | None = None # 需编译时的 sh -c 脚本（如 java 先 javac 再 java）


async def _run_code(sandbox: Sandbox, spec: LangSpec, code: str,
                    timeout: float, max_chars: int) -> str:
    """把源码写进（按语言路由后的）容器并执行；非零退出/超时 → ToolError。"""
    box = sandbox
    route = getattr(sandbox, "sandbox_for", None)
    if route is not None:                       # RoutingSandbox：取到语言专属容器
        box = await route(spec.language)
    await box.write_file(spec.filename, code)   # SandboxError→is_error
    cmd = spec.argv if spec.argv is not None else ["sh", "-c", spec.shell]
    res = await box.exec(cmd, timeout)
    out = format_exec(res, max_chars)
    if res.exit_code != 0 or res.timed_out:     # 非零退出/超时 → 标记失败
        raise ToolError(out)
    return out


class _CodeTool(Tool):
    """按 LangSpec 在沙箱内跑某种语言代码的通用基类。"""
    spec: LangSpec

    class Params(BaseModel):
        code: str

    def __init__(self, sandbox: Sandbox, timeout: float = 30.0, max_chars: int = 8000) -> None:
        self._sandbox = sandbox
        self._timeout = timeout
        self._max_chars = max_chars

    async def run(self, params: "_CodeTool.Params") -> str:
        return await _run_code(self._sandbox, self.spec, params.code,
                               self._timeout, self._max_chars)


class RunPythonTool(_CodeTool):
    name = "run_python"
    description = "在沙箱内执行 Python 代码，返回输出。"
    spec = LangSpec("python", "_run.py", ["python3", "_run.py"])

    def __init__(self, sandbox: Sandbox, timeout: float = 30.0,
                 max_chars: int = 8000, python_cmd: str = "python3") -> None:
        super().__init__(sandbox, timeout, max_chars)
        # 兼容旧签名：允许自定义 python 解释器命令
        if python_cmd != "python3":
            self.spec = LangSpec("python", "_run.py", [python_cmd, "_run.py"])


class RunNodeTool(_CodeTool):
    name = "run_node"
    description = "在沙箱内执行 Node.js 代码，返回输出。"
    spec = LangSpec("node", "_run.js", ["node", "_run.js"])


class RunJavaTool(_CodeTool):
    name = "run_java"
    description = ("在沙箱内执行 Java 代码，返回输出。"
                   "入口类必须命名为 public class Main（含 public static void main）。")
    spec = LangSpec("java", "Main.java", None, shell="javac Main.java && java Main")

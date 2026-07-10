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


async def _run_code(sandbox: Sandbox, spec: LangSpec, code: str, version: str | None,
                    timeout: float, max_chars: int) -> str:
    """执行某语言代码；非零退出/超时 → ToolError。

    会话代理（有 run_code，SandboxProxy）：按语言[+版本]起一次性子沙箱，跑完销毁、
    产物回传会话基础容器（子沙箱未配镜像时回退基础容器/路由容器）。
    否则（直接注入 DockerSandbox/RoutingSandbox/LocalSandbox，测试用）：保留原
    路由/直连逻辑。
    """
    run_code = getattr(sandbox, "run_code", None)
    if run_code is not None:
        res = await run_code(spec.language, version, spec.filename, code,
                             spec.argv, spec.shell, timeout)   # SandboxError→is_error
    else:
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
        version: str | None = None   # 语言版本（如 java 的 8/17/21）；空=用默认版本镜像

    def __init__(self, sandbox: Sandbox, timeout: float = 30.0, max_chars: int = 8000) -> None:
        self._sandbox = sandbox
        self._timeout = timeout
        self._max_chars = max_chars

    async def run(self, params: "_CodeTool.Params") -> str:
        return await _run_code(self._sandbox, self.spec, params.code, params.version,
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
                   "入口类必须命名为 public class Main（含 public static void main）。"
                   "可选 version 指定 JDK 版本（如 8/11/17/21，取决于服务端 sandbox_lang_images 配置），"
                   "空则用默认 Java 镜像。")
    spec = LangSpec("java", "Main.java", None, shell="javac Main.java && java Main")

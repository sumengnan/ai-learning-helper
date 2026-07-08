# src/harness/tools/builtins/fs_tools.py
from __future__ import annotations

from pydantic import BaseModel

from ..base import Tool
from ...sandbox.base import Sandbox
from ._sandbox_util import truncate


class WriteFileTool(Tool):
    name = "write_file"
    description = "在沙箱工作区写入文件（路径限工作区内）。"

    class Params(BaseModel):
        path: str
        content: str

    def __init__(self, sandbox: Sandbox) -> None:
        self._sandbox = sandbox

    async def run(self, params: "WriteFileTool.Params") -> str:
        await self._sandbox.write_file(params.path, params.content)  # SandboxError→is_error
        return f"已写入 {params.path}（{len(params.content)} 字符）。"


class ReadFileTool(Tool):
    name = "read_file"
    description = "读取沙箱工作区的文件。"

    class Params(BaseModel):
        path: str

    def __init__(self, sandbox: Sandbox, max_chars: int = 8000) -> None:
        self._sandbox = sandbox
        self._max_chars = max_chars

    async def run(self, params: "ReadFileTool.Params") -> str:
        content = await self._sandbox.read_file(params.path)  # SandboxError/FileNotFound→is_error
        return truncate(content, self._max_chars)


class ListFilesTool(Tool):
    name = "list_files"
    description = "列出沙箱工作区目录内容。"

    class Params(BaseModel):
        path: str = "."

    def __init__(self, sandbox: Sandbox) -> None:
        self._sandbox = sandbox

    async def run(self, params: "ListFilesTool.Params") -> str:
        files = await self._sandbox.list_files(params.path)
        return "\n".join(files) if files else "（空目录）"

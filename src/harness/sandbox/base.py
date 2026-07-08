# src/harness/sandbox/base.py
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False


class SandboxError(Exception):
    ...


def resolve_in_workspace(workspace: str, path: str) -> str:
    """把 path 规约到 workspace 内的绝对路径；逃逸（../、绝对路径、符号链接）抛 SandboxError。"""
    workspace_real = os.path.realpath(workspace)
    candidate = path if os.path.isabs(path) else os.path.join(workspace_real, path)
    real = os.path.realpath(candidate)
    if real != workspace_real and not real.startswith(workspace_real + os.sep):
        raise SandboxError(f"路径逃逸工作区：{path}")
    return real


@runtime_checkable
class Sandbox(Protocol):
    workspace: str

    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def exec(self, command: list[str], timeout: float) -> ExecResult: ...
    async def write_file(self, path: str, content: str) -> None: ...
    async def read_file(self, path: str) -> str: ...
    async def list_files(self, path: str = ".") -> list[str]: ...

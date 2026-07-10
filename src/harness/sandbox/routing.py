# src/harness/sandbox/routing.py
"""按语言路由到不同镜像的多容器沙箱。

一个 Docker 容器只能是一个镜像，因此「按语言选镜像」必然意味着多容器。RoutingSandbox
持有 语言->镜像 表，每个镜像惰性启动一个 DockerSandbox 并按语言缓存。它实现 Sandbox
协议：协议方法（exec/write_file/read_file/list_files）全部委托给「默认语言容器」，使
RunShellTool / WriteFileTool / ReadFileTool / ListFilesTool / SandboxedHttpRequestTool
零改动继续共用同一个工作区。代码工具（run_python/run_node/run_java）用新增的
sandbox_for(language) 取到对应语言容器，各自 write+exec，自洽。

已知限制：语言容器与默认容器工作区各自独立 tmpfs，互不可见——跨语言/跨容器的文件
传递不支持（例如 write_file 后用 run_java 读不到），属已知取舍。
"""
from __future__ import annotations

import asyncio
from typing import Callable

from .base import ExecResult, Sandbox


class RoutingSandbox:
    def __init__(self, images: dict[str, str], default_language: str,
                 factory: Callable[[str], Sandbox]) -> None:
        if not images:
            raise ValueError("RoutingSandbox 需要非空的 images 映射")
        self._images = dict(images)
        self._default = default_language if default_language in images else next(iter(images))
        self._factory = factory                       # language -> DockerSandbox(image=images[language])
        self._boxes: dict[str, Sandbox] = {}
        self._lock = asyncio.Lock()
        # 立即（不 start）创建默认容器，以同步暴露 workspace 属性（Sandbox 协议要求）
        self._boxes[self._default] = factory(self._default)
        self.workspace = self._boxes[self._default].workspace

    async def sandbox_for(self, language: str) -> Sandbox:
        """取到指定语言的容器（惰性创建并 start）；未知语言回退默认容器。"""
        lang = language if language in self._images else self._default
        async with self._lock:                        # 防并发首调重复创建同一语言容器
            box = self._boxes.get(lang)
            if box is None:
                box = self._factory(lang)
                self._boxes[lang] = box
        await box.start()
        return box

    async def start(self) -> None:
        await self._boxes[self._default].start()

    async def close(self) -> None:
        for box in list(self._boxes.values()):
            await box.close()
        self._boxes.clear()

    async def exec(self, command: list[str], timeout: float) -> ExecResult:
        return await self._boxes[self._default].exec(command, timeout)

    async def write_file(self, path: str, content: str) -> None:
        await self._boxes[self._default].write_file(path, content)

    async def read_file(self, path: str) -> str:
        return await self._boxes[self._default].read_file(path)

    async def list_files(self, path: str = ".") -> list[str]:
        return await self._boxes[self._default].list_files(path)

# app/sandbox_manager.py
"""会话级沙箱：把「全进程共享单容器」改为「按会话隔离容器」。

装配期仍然只把一个 sandbox 对象绑进所有工具，但这个对象是 SandboxProxy——它按
「当前会话」（contextvar）解析出真实的 Sandbox 实例，真实实例由 SandboxManager
按 conv_id 惰性创建/缓存/销毁。于是所有沙箱工具（write_file/read_file/list_files/
run_shell/run_python/... 及浏览器）零改动即获得会话隔离。

生命周期：
- 首次在某会话内执行沙箱操作时惰性创建容器（沿用 DockerSandbox 的惰性 start）。
- 删除会话 → SandboxManager.destroy(conv_id) 销毁其容器（含 /workspace tmpfs）。
- app 关停 → close_all()。
- 安全阀：空闲驱逐（sandbox_idle_timeout），防止会话容器无限期驻留。
- 容器打标签 mcp_sandbox=true + conv_id，供进程重启后 sweep_orphans() 回收孤儿。
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass, field

from harness.sandbox.base import SandboxError
from harness.sandbox.factory import build_sandbox

_log = logging.getLogger("app.sandbox")

# 当前请求所属会话；由 chat 处理器在 pump() 内 set，工具执行都在此上下文内。
_current_conv: ContextVar[str | None] = ContextVar("sandbox_conv", default=None)

_SANDBOX_LABEL = "mcp_sandbox"


def set_sandbox_conv(conv_id: str):
    """进入某会话的沙箱上下文，返回 token 供 reset。"""
    return _current_conv.set(conv_id)


def reset_sandbox_conv(token) -> None:
    _current_conv.reset(token)


@dataclass
class _Entry:
    box: object
    last_used: float = field(default_factory=time.monotonic)


class SandboxManager:
    """按 conv_id 管理真实 Sandbox 实例的生命周期。"""

    def __init__(self, config) -> None:
        self._config = config
        self._boxes: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()
        self._idle_timeout = float(getattr(config, "sandbox_idle_timeout", 0) or 0)

    async def get(self, conv_id: str):
        """取（或惰性创建）该会话的 Sandbox，并刷新其空闲计时。"""
        if not conv_id:
            raise SandboxError("无当前会话上下文，无法解析沙箱")
        async with self._lock:
            await self._evict_idle(keep=conv_id)
            entry = self._boxes.get(conv_id)
            if entry is None:
                labels = {_SANDBOX_LABEL: "true", "conv_id": conv_id}
                entry = _Entry(box=build_sandbox(self._config, labels=labels))
                self._boxes[conv_id] = entry
            entry.last_used = time.monotonic()
            return entry.box

    async def destroy(self, conv_id: str) -> None:
        """销毁某会话的容器（删除会话时调用）；不存在则静默。"""
        async with self._lock:
            entry = self._boxes.pop(conv_id, None)
        if entry is not None:
            await self._safe_close(entry.box, conv_id)

    async def close_all(self) -> None:
        """关停时销毁全部会话容器。"""
        async with self._lock:
            items = list(self._boxes.items())
            self._boxes.clear()
        for conv_id, entry in items:
            await self._safe_close(entry.box, conv_id)

    async def _evict_idle(self, keep: str) -> None:
        """惰性驱逐空闲超时的会话容器（在锁内调用）。keep 为本次要用的会话，不驱逐。"""
        if self._idle_timeout <= 0:
            return
        now = time.monotonic()
        stale = [cid for cid, e in self._boxes.items()
                 if cid != keep and now - e.last_used > self._idle_timeout]
        for cid in stale:
            entry = self._boxes.pop(cid, None)
            if entry is not None:
                await self._safe_close(entry.box, cid)

    @staticmethod
    async def _safe_close(box, conv_id: str) -> None:
        try:
            await box.close()
        except Exception as e:  # 清理失败不应打断删除/关停流程
            _log.warning("销毁会话 %s 沙箱失败：%s", conv_id, e)

    def sweep_orphans(self) -> None:
        """进程启动时回收上次遗留的会话沙箱容器（按标签）。best-effort，同步执行。

        刚启动时内存映射为空，daemon 上带 mcp_sandbox 标签的容器必是上次遗留的孤儿。
        """
        cfg = self._config
        if getattr(cfg, "sandbox_backend", "") != "docker" or not cfg.sandbox_docker_host:
            return
        try:
            import docker
            from docker.tls import TLSConfig
            client_cert = ((cfg.sandbox_docker_tls_client_cert, cfg.sandbox_docker_tls_client_key)
                           if cfg.sandbox_docker_tls_client_cert and cfg.sandbox_docker_tls_client_key
                           else None)
            tls = TLSConfig(client_cert=client_cert,
                            ca_cert=cfg.sandbox_docker_tls_ca_cert or None,
                            verify=cfg.sandbox_docker_tls_verify)
            client = docker.DockerClient(base_url=cfg.sandbox_docker_host, tls=tls)
            try:
                leftovers = client.containers.list(
                    all=True, filters={"label": f"{_SANDBOX_LABEL}=true"})
                for c in leftovers:
                    try:
                        c.remove(force=True)
                    except Exception as e:
                        _log.warning("回收孤儿容器 %s 失败：%s", getattr(c, "id", "?"), e)
                if leftovers:
                    _log.info("启动清扫：回收了 %d 个遗留沙箱容器", len(leftovers))
            finally:
                client.close()
        except Exception as e:  # daemon 不可达等，不阻断启动
            _log.warning("启动清扫沙箱孤儿容器失败：%s", e)


class SandboxProxy:
    """实现 Sandbox 协议：按当前会话上下文把调用委托给真实的会话容器。

    close() 为空实现——真实容器的生命周期归 SandboxManager（destroy/close_all/空闲驱逐）。
    仅当配置了多镜像路由（sandbox_images）时才暴露 sandbox_for，以保持 assembly 里
    「有 sandbox_for 才注册多语言代码工具」的判定不变。
    """

    def __init__(self, manager: SandboxManager) -> None:
        self._m = manager
        self.workspace = manager._config.sandbox_workspace
        if getattr(manager._config, "sandbox_images", None):
            self.sandbox_for = self._sandbox_for  # 路由沙箱下才暴露

    async def _box(self):
        return await self._m.get(_current_conv.get())

    async def start(self) -> None:
        await (await self._box()).start()

    async def close(self) -> None:
        # 生命周期归 manager；单容器的销毁走 destroy/close_all，这里不动。
        return None

    async def exec(self, command: list, timeout: float):
        return await (await self._box()).exec(command, timeout)

    async def write_file(self, path: str, content: str) -> None:
        await (await self._box()).write_file(path, content)

    async def read_file(self, path: str) -> str:
        return await (await self._box()).read_file(path)

    async def list_files(self, path: str = ".") -> list:
        return await (await self._box()).list_files(path)

    async def _sandbox_for(self, language: str):
        return await (await self._box()).sandbox_for(language)

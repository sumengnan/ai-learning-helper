import time

import pytest

from app.config import AppConfig
from app.sandbox_manager import (
    SandboxManager, SandboxProxy, set_sandbox_conv, reset_sandbox_conv)
from harness.sandbox.base import SandboxError


def _cfg(**kw):
    # local 后端：build_sandbox 返回 LocalSandbox（临时目录 + 子进程），无需 Docker
    return AppConfig(api_key="k", sandbox_backend="local", app_db_path=":memory:", **kw)


async def test_get_caches_per_conversation():
    m = SandboxManager(_cfg())
    a1 = await m.get("conv-a")
    a2 = await m.get("conv-a")
    b1 = await m.get("conv-b")
    assert a1 is a2                 # 同会话复用同一容器
    assert a1 is not b1             # 不同会话相互隔离


async def test_destroy_removes_and_recreates():
    m = SandboxManager(_cfg())
    a1 = await m.get("conv-a")
    await m.destroy("conv-a")
    assert "conv-a" not in m._boxes
    a2 = await m.get("conv-a")      # 删后再取是全新实例
    assert a2 is not a1
    await m.destroy("nope")         # 不存在的会话静默


async def test_close_all_clears():
    m = SandboxManager(_cfg())
    await m.get("conv-a")
    await m.get("conv-b")
    await m.close_all()
    assert m._boxes == {}


async def test_idle_eviction_drops_stale_only():
    m = SandboxManager(_cfg(sandbox_idle_timeout=100.0))
    await m.get("stale")
    fresh = await m.get("fresh")
    m._boxes["stale"].last_used = time.monotonic() - 1000   # 把 stale 手动置为超时
    # 触碰任一会话即惰性驱逐超时者；fresh 不受影响
    again = await m.get("fresh")
    assert "stale" not in m._boxes
    assert again is fresh


async def test_idle_eviction_disabled_when_zero():
    m = SandboxManager(_cfg(sandbox_idle_timeout=0))
    await m.get("a")
    m._boxes["a"].last_used = time.monotonic() - 10_000
    await m.get("b")
    assert "a" in m._boxes           # 关闭空闲驱逐时不清理


async def test_proxy_delegates_to_current_conversation():
    m = SandboxManager(_cfg())
    proxy = SandboxProxy(m)
    t = set_sandbox_conv("conv-a")
    try:
        await proxy.write_file("note.txt", "hello")
        assert await proxy.read_file("note.txt") == "hello"
        assert "note.txt" in await proxy.list_files(".")
    finally:
        reset_sandbox_conv(t)
    # 写入的是 conv-a 的容器；conv-b 看不到
    t = set_sandbox_conv("conv-b")
    try:
        assert "note.txt" not in await proxy.list_files(".")
    finally:
        reset_sandbox_conv(t)
    await m.close_all()


async def test_proxy_without_context_raises():
    proxy = SandboxProxy(SandboxManager(_cfg()))
    with pytest.raises(SandboxError):
        await proxy.write_file("x", "y")


def test_proxy_exposes_sandbox_for_only_when_routing():
    # 单镜像（sandbox_images 空）：不暴露 sandbox_for（保持 assembly 多语言工具注册判定不变）。
    # 显式 _env_file=None + sandbox_images={}：config 默认已预置多镜像映射，且不依赖 .env，
    # 否则在无 .env 的环境（如 worktree）下会读到非空默认导致本测试假阴/假阳。
    assert getattr(SandboxProxy(SandboxManager(
        _cfg(_env_file=None, sandbox_images={}))), "sandbox_for", None) is None
    routed = _cfg(_env_file=None,
                  sandbox_images={"python": "python:3.12-slim", "node": "node:20-slim"})
    assert getattr(SandboxProxy(SandboxManager(routed)), "sandbox_for", None) is not None

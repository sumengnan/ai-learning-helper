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
    a1 = await m.get_lang("conv-a")
    a2 = await m.get_lang("conv-a")
    b1 = await m.get_lang("conv-b")
    assert a1 is a2                 # 同会话同语言复用同一容器
    assert a1 is not b1             # 不同会话相互隔离


async def test_destroy_removes_and_recreates():
    m = SandboxManager(_cfg())
    a1 = await m.get_lang("conv-a")
    await m.destroy("conv-a")
    assert not any(k[0] == "conv-a" for k in m._boxes)
    a2 = await m.get_lang("conv-a")      # 删后再取是全新实例
    assert a2 is not a1
    await m.destroy("nope")         # 不存在的会话静默


async def test_close_all_clears():
    m = SandboxManager(_cfg())
    await m.get_lang("conv-a")
    await m.get_lang("conv-b")
    await m.close_all()
    assert m._boxes == {}


async def test_idle_eviction_drops_stale_only():
    m = SandboxManager(_cfg(sandbox_idle_timeout=100.0))
    await m.get_lang("stale")
    fresh = await m.get_lang("fresh")
    # 本地后端所有语言共用 label "local"；把 stale 手动置为超时
    m._boxes[("stale", "local")].last_used = time.monotonic() - 1000
    again = await m.get_lang("fresh")    # 触碰任一会话即惰性驱逐超时者；fresh 不受影响
    assert not any(k[0] == "stale" for k in m._boxes)
    assert again is fresh


async def test_idle_eviction_disabled_when_zero():
    m = SandboxManager(_cfg(sandbox_idle_timeout=0))
    await m.get_lang("a")
    m._boxes[("a", "local")].last_used = time.monotonic() - 10_000
    await m.get_lang("b")
    assert any(k[0] == "a" for k in m._boxes)   # 关闭空闲驱逐时不清理


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


async def test_proxy_exec_forwards_quiet_kwarg():
    # DNS 解析等内部动作用 quiet=True 走静默路径；代理必须转发该 kwarg，
    # 否则有沙箱时 sandbox_dns.resolve_host 会因 SandboxProxy.exec() 不认 quiet 而报错。
    m = SandboxManager(_cfg())
    proxy = SandboxProxy(m)
    t = set_sandbox_conv("conv-a")
    try:
        res = await proxy.exec(["echo", "ok"], 10, quiet=True)
        assert "ok" in res.stdout
    finally:
        reset_sandbox_conv(t)
    await m.close_all()


async def test_proxy_without_context_raises():
    proxy = SandboxProxy(SandboxManager(_cfg()))
    with pytest.raises(SandboxError):
        await proxy.write_file("x", "y")


async def test_proxy_for_language_and_shell_default_share_local_box():
    # 本地后端：所有语言共用一个 local 沙箱。for_language(任意) 与协议方法（→shell）取到同一个。
    m = SandboxManager(_cfg())
    proxy = SandboxProxy(m)
    t = set_sandbox_conv("conv-a")
    try:
        via_lang = await proxy.for_language("python")
        via_shell = await proxy.for_language()          # 缺省 → shell（本地共用 local）
        assert via_lang is via_shell
    finally:
        reset_sandbox_conv(t)
    await m.close_all()


async def test_proxy_set_uploads_seeds_into_container():
    m = SandboxManager(_cfg())
    proxy = SandboxProxy(m)
    t = set_sandbox_conv("conv-a")
    try:
        await proxy.set_uploads([("uploads/x.txt", b"hi")])
        box = await proxy.for_language("python")
        assert "x.txt" in await box.list_files("uploads")
    finally:
        reset_sandbox_conv(t)
    await m.close_all()


def test_guide_tells_model_to_use_save_download_for_deliverables():
    """回归：模型把 write_file→save_download 当固定流水线用，白跑一次往返；子步一重试
    这套组合还会整个再来一遍。沙箱里的文件是过程中间物、随沙箱销毁，用户根本拿不到。"""
    from types import SimpleNamespace
    from app.sandbox_manager import sandbox_guide
    g = sandbox_guide(SimpleNamespace(sandbox_workspace="/workspace", sandbox_backend="local"))
    assert "成品文件直接用 save_download" in g
    assert "不需要" in g and "write_file" in g
    assert "用户拿不到" in g
    assert "后续步骤还要在沙箱里读取" in g   # 保留正当用法，不是一刀切禁用

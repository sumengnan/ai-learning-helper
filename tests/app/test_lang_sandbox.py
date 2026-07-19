"""语言/版本一次性子沙箱（SandboxProxy.run_code）——不碰 Docker，用 stub 子沙箱。"""
import pytest

import app.sandbox_manager as sm
from app.config import AppConfig
from app.sandbox_manager import (
    SandboxManager, SandboxProxy, reset_sandbox_conv, set_sandbox_conv)
from harness.sandbox.base import ExecResult, SandboxError
from harness.tools.base import ToolExecutor, ToolRegistry
from harness.tools.builtins.code_tool import RunJavaTool, RunPythonTool
from harness.types import ToolCall

_LANG_IMAGES = {"python": "python:3.12-slim", "java": "eclipse-temurin:21-jdk",
                "java8": "eclipse-temurin:8-jdk", "java21": "eclipse-temurin:21-jdk"}


def _cfg(**kw):
    return AppConfig(api_key="k", sandbox_backend="local", app_db_path=":memory:",
                     sandbox_lang_images=_LANG_IMAGES, **kw)


class _StubSub:
    """假的一次性子沙箱：记录镜像/生命周期/执行；exec 时可产出产物文件。"""
    made: list["_StubSub"] = []

    def __init__(self, image, produce=None):
        self.image = image
        self.workspace = "/workspace"
        self.started = 0
        self.closed = 0
        self.execs = []
        self.files: dict[str, str] = {}
        self._produce = produce or {}
        _StubSub.made.append(self)

    async def start(self):
        self.started += 1

    async def close(self):
        self.closed += 1

    async def exec(self, command, timeout):
        self.execs.append(command)
        self.files.update(self._produce)      # 模拟执行产出文件
        return ExecResult("sub-out", "", 0)

    async def write_file(self, path, content):
        self.files[path] = content

    async def read_file(self, path):
        if path not in self.files:
            raise SandboxError(path)
        return self.files[path]

    async def list_files(self, path="."):
        return sorted(self.files)


@pytest.fixture
def _conv():
    tok = set_sandbox_conv("conv-x")
    yield
    reset_sandbox_conv(tok)


@pytest.fixture
def _stub_docker(monkeypatch):
    _StubSub.made = []
    captured = {}

    def fake_docker_for(config, image, labels=None, network=None, display_name="基础沙箱"):
        captured["image"] = image
        captured["labels"] = labels
        captured["network"] = network
        captured["display_name"] = display_name
        return _StubSub(image, produce=captured.get("produce"))
    monkeypatch.setattr(sm, "_docker_for", fake_docker_for)
    return captured


async def test_run_code_routes_version_to_cached_sub(_conv, _stub_docker):
    proxy = SandboxProxy(SandboxManager(_cfg()))
    res = await proxy.run_code("java", "8", "Main.java", "class Main{}",
                               None, "javac Main.java && java Main", timeout=5)
    assert res.exit_code == 0
    assert _stub_docker["image"] == "eclipse-temurin:8-jdk"     # version=8 → java8 镜像
    assert _stub_docker["network"] == "none"                    # 子沙箱默认禁网
    assert _stub_docker["labels"]["role"] == "lang"             # 缓存复用（非一次性）
    sub = _StubSub.made[0]
    assert sub.started == 1 and sub.closed == 0                 # 用完不销毁（缓存复用）
    assert sub.execs == [["sh", "-c", "javac Main.java && java Main"]]


async def test_cached_sub_reused_across_runs_not_rebuilt(_conv, _stub_docker):
    """同会话同语言连续执行：复用同一子沙箱，不重建、不销毁（避免反复重建镜像容器）。"""
    proxy = SandboxProxy(SandboxManager(_cfg()))
    await proxy.run_code("python", None, "a.py", "print(1)", ["python3", "a.py"], None, timeout=5)
    await proxy.run_code("python", None, "b.py", "print(2)", ["python3", "b.py"], None, timeout=5)
    assert len(_StubSub.made) == 1                              # 只建了一个容器
    assert _StubSub.made[0].closed == 0                         # 期间从未销毁
    assert len(_StubSub.made[0].execs) == 2                     # 两次执行落在同一容器


async def test_different_langs_get_separate_cached_subs(_conv, _stub_docker):
    proxy = SandboxProxy(SandboxManager(_cfg()))
    await proxy.run_code("python", None, "a.py", "print(1)", ["python3", "a.py"], None, timeout=5)
    await proxy.run_code("java", "21", "Main.java", "class Main{}", None, "true", timeout=5)
    assert len(_StubSub.made) == 2                              # 不同语言各自一个子沙箱
    assert {s.image for s in _StubSub.made} == {"python:3.12-slim", "eclipse-temurin:21-jdk"}


async def test_destroy_conv_also_closes_its_cached_subs(_conv, _stub_docker):
    mgr = SandboxManager(_cfg())
    proxy = SandboxProxy(mgr)
    await proxy.run_code("python", None, "a.py", "print(1)", ["python3", "a.py"], None, timeout=5)
    sub = _StubSub.made[0]
    assert sub.closed == 0
    await mgr.destroy("conv-x")                                 # 删除会话 → 连同子沙箱一并销毁
    assert sub.closed == 1
    assert mgr._subs == {}


async def test_idle_cached_sub_evicted_after_timeout(_conv, _stub_docker):
    mgr = SandboxManager(_cfg())
    proxy = SandboxProxy(mgr)
    await proxy.run_code("python", None, "a.py", "print(1)", ["python3", "a.py"], None, timeout=5)
    sub = _StubSub.made[0]
    mgr._subs[("conv-x", "python")].last_used = 0.0            # 假装该子沙箱早已空闲超时
    await mgr.get("other-conv")                                 # 别的会话活动触发惰性驱逐
    assert sub.closed == 1                                      # 空闲超时 → 自动销毁
    assert ("conv-x", "python") not in mgr._subs


async def test_close_all_closes_cached_subs(_conv, _stub_docker):
    mgr = SandboxManager(_cfg())
    proxy = SandboxProxy(mgr)
    await proxy.run_code("python", None, "a.py", "print(1)", ["python3", "a.py"], None, timeout=5)
    sub = _StubSub.made[0]
    await mgr.close_all()
    assert sub.closed == 1 and mgr._subs == {}


async def test_caching_off_restores_ephemeral_behavior(_conv, _stub_docker):
    """sandbox_sub_idle_timeout<=0：退回「用完即销毁」旧语义（role=ephemeral、closed=1）。"""
    proxy = SandboxProxy(SandboxManager(_cfg(sandbox_sub_idle_timeout=0)))
    await proxy.run_code("python", None, "a.py", "print(1)", ["python3", "a.py"], None, timeout=5)
    assert _stub_docker["labels"]["role"] == "ephemeral"
    sub = _StubSub.made[0]
    assert sub.closed == 1                                      # 关缓存：用完即销毁
    # 再跑一次应新建一个（不复用）
    await proxy.run_code("python", None, "b.py", "print(2)", ["python3", "b.py"], None, timeout=5)
    assert len(_StubSub.made) == 2


async def test_run_code_copies_artifacts_back_to_base(_conv, _stub_docker):
    _stub_docker["produce"] = {"out.txt": "done"}               # 子沙箱执行时产出 out.txt
    mgr = SandboxManager(_cfg())
    proxy = SandboxProxy(mgr)
    base = await mgr.get("conv-x")
    await base.write_file("in.txt", "hi")                       # 基础工作区先有输入
    await proxy.run_code("python", None, "_run.py", "print(1)",
                         ["python3", "_run.py"], None, timeout=5)
    sub = _StubSub.made[0]
    assert sub.files.get("in.txt") == "hi"                      # 执行前：输入拷进子沙箱
    assert await base.read_file("out.txt") == "done"            # 执行后：产物拷回基础容器


async def test_run_code_explicit_unknown_version_errors(_conv, _stub_docker):
    proxy = SandboxProxy(SandboxManager(_cfg()))
    with pytest.raises(SandboxError):
        await proxy.run_code("java", "99", "Main.java", "x", None, "true", timeout=5)
    assert _StubSub.made == []                                  # 未知版本：不起子沙箱


async def test_run_code_falls_back_to_base_when_lang_not_configured(_conv, _stub_docker):
    # lang_images 里没有 ruby、也没给 version → 回退到会话基础容器（真 LocalSandbox）执行
    proxy = SandboxProxy(SandboxManager(_cfg()))
    res = await proxy.run_code("ruby", None, "s.sh", "echo hi",
                               ["sh", "-c", "echo hi"], None, timeout=5)
    assert "hi" in res.stdout
    assert _StubSub.made == []                                  # 未起子沙箱


async def test_run_java_tool_end_to_end_via_proxy(_conv, _stub_docker):
    proxy = SandboxProxy(SandboxManager(_cfg()))
    reg = ToolRegistry()
    reg.register(RunJavaTool(proxy, timeout=5))
    ex = ToolExecutor(reg)
    r = await ex.execute(ToolCall(id="c1", name="run_java",
                                  arguments={"code": "class Main{}", "version": "21"}))
    assert r.is_error is False and "sub-out" in r.content
    assert _StubSub.made[0].image == "eclipse-temurin:21-jdk"


async def test_run_python_tool_without_proxy_runs_in_base_sandbox():
    # 直接注入普通 LocalSandbox（无 run_code）→ 保留原直连逻辑
    from harness.sandbox.local import LocalSandbox
    sb = LocalSandbox(); await sb.start()
    try:
        reg = ToolRegistry(); reg.register(RunPythonTool(sb, timeout=5))
        r = await ToolExecutor(reg).execute(ToolCall(
            id="c1", name="run_python", arguments={"code": "print(3+4)"}))
        assert r.is_error is False and "7" in r.content
    finally:
        await sb.close()

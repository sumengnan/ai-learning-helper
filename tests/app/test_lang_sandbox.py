"""按语言容器（SandboxProxy.for_language / SandboxManager.get_lang）——不碰 Docker，用 stub 容器。

新模型：AI 要跑哪种语言就自动起哪种语言的容器（run_python→python 容器、run_shell→shell 容器…），
各 (会话,语言) 一个独立容器、缓存复用、1h 空闲销毁；文件工具带 language 落对应容器、缺省 shell。
"""
import time as _t

import pytest

import app.sandbox_manager as sm
from app.config import AppConfig
from app.sandbox_manager import (
    SandboxManager, SandboxProxy, reset_sandbox_conv, set_sandbox_conv)
from harness.sandbox.base import ExecResult, SandboxError
from harness.tools.base import ToolExecutor, ToolRegistry
from harness.tools.builtins.code_tool import RunJavaTool, RunPythonTool
from harness.tools.builtins.fs_tools import ReadFileTool, WriteFileTool
from harness.types import ToolCall

_LANG_IMAGES = {"python": "python:3.12-slim", "java": "eclipse-temurin:21-jdk",
                "java8": "eclipse-temurin:8-jdk", "java21": "eclipse-temurin:21-jdk"}


def _cfg(**kw):
    # docker 后端：语言容器经 build_sandbox 造（下面 fixture 打桩），不连真实 daemon
    return AppConfig(api_key="k", sandbox_backend="docker", app_db_path=":memory:",
                     sandbox_docker_host="tcp://stub:2376", sandbox_shell_image="debian:12-slim",
                     sandbox_lang_images=_LANG_IMAGES, _env_file=None, **kw)


class _StubBox:
    """假容器：记录镜像/label/生命周期/执行与文件。for_language 返回自身（供直连用例）。"""
    made: list["_StubBox"] = []

    def __init__(self, image=None, label=None):
        self.image = image
        self.label = label
        self.workspace = "/workspace"
        self.started = 0
        self.closed = 0
        self.execs: list = []
        self.files: dict = {}
        _StubBox.made.append(self)

    async def start(self):
        self.started += 1

    async def close(self):
        self.closed += 1

    async def for_language(self, language=None, version=None):
        await self.start()
        return self

    async def exec(self, command, timeout, *, quiet=False):
        self.execs.append(command)
        return ExecResult("out", "", 0)

    async def write_file(self, path, content):
        self.files[path] = content

    async def write_bytes(self, path, data):
        self.files[path] = data

    async def read_file(self, path):
        if path not in self.files:
            raise SandboxError(path)
        v = self.files[path]
        return v if isinstance(v, str) else v.decode()

    async def list_files(self, path="."):
        return sorted(self.files)


@pytest.fixture
def _conv():
    tok = set_sandbox_conv("conv-x")
    yield
    reset_sandbox_conv(tok)


@pytest.fixture
def _stub(monkeypatch):
    """打桩 build_sandbox（语言容器）与 _docker_for（浏览器容器），都造 _StubBox。"""
    _StubBox.made = []
    calls: list = []
    captured: dict = {}

    def fake_build(config, labels=None, *, image=None, network=None,
                   display_name="沙箱", mem_limit=None):
        calls.append({"image": image, "labels": labels or {}, "display_name": display_name})
        return _StubBox(image=image, label=(labels or {}).get("lang"))

    def fake_docker_for(config, image, labels=None, network=None,
                        display_name="沙箱", mem_limit=None):
        captured.update(image=image, labels=labels, network=network,
                        display_name=display_name, mem_limit=mem_limit)
        return _StubBox(image=image, label=(labels or {}).get("role"))

    monkeypatch.setattr(sm, "build_sandbox", fake_build)
    monkeypatch.setattr(sm, "_docker_for", fake_docker_for)
    return {"calls": calls, "browser": captured}


def _lang_boxes():
    """本次建的语言/ shell 容器（排除浏览器 role=browser-global）。"""
    return [b for b in _StubBox.made if b.label != "browser-global"]


# ---- for_language / get_lang：按语言起容器 ----

async def test_for_language_routes_version_to_image(_conv, _stub):
    proxy = SandboxProxy(SandboxManager(_cfg()))
    box = await proxy.for_language("java", "8")           # version=8 → java8 镜像
    assert box.image == "eclipse-temurin:8-jdk"
    assert box.label == "java8" and box.started == 1
    assert _stub["calls"][-1]["labels"]["lang"] == "java8"


async def test_same_conv_lang_reused_across_runs(_conv, _stub):
    """同会话同语言连续 run_python：复用同一容器，不重建、不销毁。"""
    proxy = SandboxProxy(SandboxManager(_cfg()))
    reg = ToolRegistry(); reg.register(RunPythonTool(proxy, timeout=5))
    ex = ToolExecutor(reg)
    await ex.execute(ToolCall(id="1", name="run_python", arguments={"code": "print(1)"}))
    await ex.execute(ToolCall(id="2", name="run_python", arguments={"code": "print(2)"}))
    boxes = _lang_boxes()
    assert len(boxes) == 1                                # 只建了一个 python 容器
    assert boxes[0].closed == 0                           # 期间从未销毁
    assert len(boxes[0].execs) == 2                       # 两次执行落同一容器
    assert boxes[0].image == "python:3.12-slim"


async def test_different_langs_get_separate_containers(_conv, _stub):
    proxy = SandboxProxy(SandboxManager(_cfg()))
    await proxy.for_language("python")
    await proxy.for_language("java", "21")
    boxes = _lang_boxes()
    assert len(boxes) == 2
    assert {b.image for b in boxes} == {"python:3.12-slim", "eclipse-temurin:21-jdk"}


async def test_unknown_version_errors(_conv, _stub):
    proxy = SandboxProxy(SandboxManager(_cfg()))
    with pytest.raises(SandboxError):
        await proxy.for_language("java", "99")            # 无 java99 镜像
    assert _lang_boxes() == []                            # 未起任何容器


async def test_unknown_language_falls_back_to_shell(_conv, _stub):
    # lang_images 里没有 ruby、也没给 version → 落 shell 容器（debian:12-slim）
    proxy = SandboxProxy(SandboxManager(_cfg()))
    box = await proxy.for_language("ruby")
    assert box.label == "shell" and box.image == "debian:12-slim"


async def test_run_java_tool_end_to_end_via_proxy(_conv, _stub):
    proxy = SandboxProxy(SandboxManager(_cfg()))
    reg = ToolRegistry(); reg.register(RunJavaTool(proxy, timeout=5))
    r = await ToolExecutor(reg).execute(ToolCall(
        id="c1", name="run_java", arguments={"code": "class Main{}", "version": "21"}))
    assert r.is_error is False and "out" in r.content
    assert _lang_boxes()[0].image == "eclipse-temurin:21-jdk"


# ---- 文件工具按 language 路由 ----

async def test_write_file_language_routes_to_that_container(_conv, _stub):
    """write_file 带 language=python → 落 python 容器；随后 run_python 能在同容器读到。"""
    proxy = SandboxProxy(SandboxManager(_cfg()))
    reg = ToolRegistry()
    reg.register(WriteFileTool(proxy)); reg.register(ReadFileTool(proxy))
    ex = ToolExecutor(reg)
    await ex.execute(ToolCall(id="1", name="write_file",
                              arguments={"path": "d.txt", "content": "hi", "language": "python"}))
    r = await ex.execute(ToolCall(id="2", name="read_file",
                                  arguments={"path": "d.txt", "language": "python"}))
    assert r.is_error is False and "hi" in r.content
    py = [b for b in _lang_boxes() if b.label == "python"]
    assert len(py) == 1 and py[0].files.get("d.txt") == "hi"   # 落在 python 容器
    assert not any(b.label == "shell" for b in _lang_boxes())  # 未落 shell


async def test_write_file_default_goes_to_shell(_conv, _stub):
    proxy = SandboxProxy(SandboxManager(_cfg()))
    reg = ToolRegistry(); reg.register(WriteFileTool(proxy))
    await ToolExecutor(reg).execute(ToolCall(
        id="1", name="write_file", arguments={"path": "d.txt", "content": "hi"}))
    boxes = _lang_boxes()
    assert len(boxes) == 1 and boxes[0].label == "shell"


# ---- 生命周期：destroy / 空闲驱逐 / close_all ----

async def test_destroy_closes_all_lang_containers(_conv, _stub):
    mgr = SandboxManager(_cfg()); proxy = SandboxProxy(mgr)
    await proxy.for_language("python")
    await proxy.for_language("java", "8")
    boxes = _lang_boxes()
    await mgr.destroy("conv-x")
    assert all(b.closed == 1 for b in boxes)
    assert mgr._boxes == {}


async def test_idle_container_evicted_after_timeout(_conv, _stub):
    mgr = SandboxManager(_cfg()); proxy = SandboxProxy(mgr)
    box = await proxy.for_language("python")
    mgr._boxes[("conv-x", "python")].last_used = _t.monotonic() - (mgr._idle_timeout + 1)
    await mgr.get_lang("other-conv", "python")            # 别的会话活动触发惰性驱逐
    assert box.closed == 1
    assert ("conv-x", "python") not in mgr._boxes


async def test_close_all_closes_lang_containers(_conv, _stub):
    mgr = SandboxManager(_cfg()); proxy = SandboxProxy(mgr)
    box = await proxy.for_language("python")
    await mgr.close_all()
    assert box.closed == 1 and mgr._boxes == {}


# ---- 上传附件：每个（新建/已存在）容器都播种 ----

async def test_set_uploads_seeds_new_and_existing_containers(_conv, _stub):
    mgr = SandboxManager(_cfg()); proxy = SandboxProxy(mgr)
    py = await proxy.for_language("python")               # 已存在的容器
    await proxy.set_uploads([("uploads/a.csv", b"1,2,3")])
    assert py.files.get("uploads/a.csv") == b"1,2,3"      # 补进已存在的容器
    node = await proxy.for_language("node")               # 之后新建的容器
    assert node.files.get("uploads/a.csv") == b"1,2,3"    # 新容器建时也播种


async def test_uploads_accumulate_across_turns(_conv, _stub):
    mgr = SandboxManager(_cfg()); proxy = SandboxProxy(mgr)
    await proxy.set_uploads([("uploads/a.txt", b"A")])    # 第一轮
    await proxy.set_uploads([("uploads/b.txt", b"B")])    # 第二轮（累加，不覆盖）
    box = await proxy.for_language("python")              # 之后建的容器两份都有
    assert box.files.get("uploads/a.txt") == b"A"
    assert box.files.get("uploads/b.txt") == b"B"


# ---- 直连普通 Sandbox（无会话管理）：for_language 返回自身 ----

async def test_run_python_tool_direct_local_sandbox():
    from harness.sandbox.local import LocalSandbox
    sb = LocalSandbox(); await sb.start()
    try:
        reg = ToolRegistry(); reg.register(RunPythonTool(sb, timeout=5))
        r = await ToolExecutor(reg).execute(ToolCall(
            id="c1", name="run_python", arguments={"code": "print(3+4)"}))
        assert r.is_error is False and "7" in r.content
    finally:
        await sb.close()


# ---- 全局浏览器沙箱：跨会话共用一个、懒加载复用、24h 空闲/关停销毁 ----

def _bcfg(**kw):
    return _cfg(browser_sandbox_image="playwright:pw",
               browser_sandbox_mem_limit="512m", **kw)


async def test_browser_is_global_singleton_reused(_stub):
    mgr = SandboxManager(_bcfg())
    box1, c1 = await mgr.get_browser()
    box2, c2 = await mgr.get_browser()
    assert box1 is box2                     # 全局一个，跨调用复用
    assert c1 is True and c2 is True        # 恒 cached=True：浏览器不得销毁它
    assert _stub["browser"]["labels"]["role"] == "browser-global"
    assert _stub["browser"]["network"] == "bridge"      # 浏览器用真实网络（sandbox_network）
    assert _stub["browser"]["mem_limit"] == "512m"      # 更大内存防 Chromium OOM


async def test_browser_closed_on_close_all(_stub):
    mgr = SandboxManager(_bcfg())
    box, _ = await mgr.get_browser()
    await mgr.close_all()
    assert box.closed == 1 and mgr._browser is None


async def test_browser_idle_evicted_and_recreated(_stub):
    mgr = SandboxManager(_bcfg())
    box1, _ = await mgr.get_browser()
    mgr._browser.last_used = _t.monotonic() - (mgr._browser_idle + 1)
    box2, _ = await mgr.get_browser()
    assert box1.closed == 1
    assert box2 is not box1


async def test_browser_idle_off_keeps_forever(_stub):
    mgr = SandboxManager(_bcfg(browser_sandbox_idle_timeout=0))
    box1, _ = await mgr.get_browser()
    mgr._browser.last_used = _t.monotonic() - 999999
    box2, _ = await mgr.get_browser()
    assert box1 is box2 and box1.closed == 0


async def test_browser_not_touched_by_conv_destroy(_stub):
    mgr = SandboxManager(_bcfg())
    box, _ = await mgr.get_browser()
    await mgr.destroy("conv-x")
    assert box.closed == 0 and mgr._browser is not None

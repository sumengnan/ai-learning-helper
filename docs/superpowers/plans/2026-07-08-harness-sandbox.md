> ⚠️ **历史设计记录（已过时）**：harness 内核已抽成外部包
> [ai-harness-framework](https://github.com/sumengnan/ai-harness-framework)（import 名仍是 `harness`）。
> 本文是带日期的设计存档，文中的 `src/harness/` 路径与打包配置反映**当时**的仓库结构、未随抽包更新；
> 当前结构以 [架构文档](../../architecture-harness.md) 为准。

# AI Harness 沙箱执行 + 外部 API（子项目③b-1）实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** `Sandbox` 隔离原语（协议 + 远程 DockerSandbox + 测试用 LocalSandbox）+ 文件/shell/代码 5 个容器工具 + 外部 API/HTTP 工具（进程内 httpx + SSRF 防护 + 白名单）。

**架构：** `sandbox/`（base 协议 + local + docker）、`net/policy`（URL 策略）、6 个工具（fs×3 / shell / code / http），工具依赖协议/策略。测试用 LocalSandbox + httpx.MockTransport + 注入式 DNS，不碰云/真实网络。

**技术栈：** 沿用①②③a · 新增 `docker[ssh]`、`httpx`。

**规格：** `docs/superpowers/specs/2026-07-08-harness-sandbox-design.md`

**提交规范：** git 身份已是 sumengnan，默认提交；**任何 commit message 不得出现 Claude/AI/Co-Authored-By 等署名**。

---

## 文件结构与职责

| 文件 | 动作 | 职责 |
|---|---|---|
| `src/harness/sandbox/base.py` | 新增 | `Sandbox` 协议 + `ExecResult` + `SandboxError` + `resolve_in_workspace()` |
| `src/harness/sandbox/local.py` | 新增 | `LocalSandbox`（临时目录+子进程，测试/离线） |
| `src/harness/sandbox/docker.py` | 新增 | `DockerSandbox`（docker SDK over SSH，远程容器） |
| `src/harness/sandbox/factory.py` | 新增 | `build_sandbox(config)` 按 backend 造实例 |
| `src/harness/net/policy.py` | 新增 | `check_url()` + `PolicyError`（白名单 + SSRF 拦截） |
| `src/harness/tools/builtins/_sandbox_util.py` | 新增 | `truncate` / `format_exec` 辅助 |
| `src/harness/tools/builtins/fs_tools.py` | 新增 | `WriteFileTool`/`ReadFileTool`/`ListFilesTool` |
| `src/harness/tools/builtins/shell_tool.py` | 新增 | `RunShellTool` |
| `src/harness/tools/builtins/code_tool.py` | 新增 | `RunPythonTool` |
| `src/harness/tools/builtins/http_tool.py` | 新增 | `HttpRequestTool` |
| `src/harness/config.py` | 改 | sandbox + http 配置 |
| `pyproject.toml` | 改 | `docker[ssh]`、`httpx` |

**错误语义**（贯穿本计划）：工具遇到**基础设施/安全错误**（路径逃逸 `SandboxError`、SSRF `PolicyError`、沙箱故障）**不自己 catch**，让其抛出 → 由②的 `ToolExecutor` 兜成 `is_error=True` 回填；**命令执行完但失败**（非零退出码/超时）是**正常结果**，返回带 exit_code 的字符串（`is_error=False`，模型据此判断）。

---

## 任务 0：依赖与配置

**文件：** 改 `pyproject.toml`、`src/harness/config.py`、测试 `tests/test_config.py`

- [ ] **步骤 1：`pyproject.toml` 的 `dependencies` 追加**

```toml
    "docker[ssh]>=7.0",
    "httpx>=0.27",
```

- [ ] **步骤 2：`uv sync`**　运行：`uv sync`　预期：装上 docker/httpx/paramiko。

- [ ] **步骤 3：写失败测试**（`tests/test_config.py` 追加）

```python
def test_sandbox_defaults():
    cfg = HarnessConfig(api_key="k")
    assert cfg.sandbox_backend == "local"
    assert cfg.sandbox_image == "python:3.12-slim"
    assert cfg.sandbox_network == "none"
    assert cfg.sandbox_exec_timeout == 30.0
    assert cfg.sandbox_output_max_chars == 8000
    assert cfg.http_allowed_domains == []
    assert cfg.http_block_private is True
    assert cfg.http_max_response_bytes == 5_000_000
    assert cfg.http_max_redirects == 5
```

运行：预期 FAIL。

- [ ] **步骤 4：改 `src/harness/config.py`** 末尾追加：

```python
    # 容器沙箱
    sandbox_backend: str = "local"          # local | docker
    sandbox_docker_host: str = ""           # ssh://user@host
    sandbox_image: str = "python:3.12-slim"
    sandbox_workspace: str = "/workspace"
    sandbox_user: str = "1000:1000"
    sandbox_network: str = "none"
    sandbox_mem_limit: str = "512m"
    sandbox_cpus: float = 1.0
    sandbox_pids_limit: int = 128
    sandbox_exec_timeout: float = 30.0
    sandbox_output_max_chars: int = 8000
    # 外部 API/HTTP
    http_allowed_domains: list = []         # 空=放行公网；非空=仅白名单
    http_block_private: bool = True         # SSRF：拦截内网/元数据
    http_timeout: float = 30.0
    http_max_response_bytes: int = 5_000_000
    http_max_redirects: int = 5
```

- [ ] **步骤 5：跑通并 commit**

运行：`uv run pytest tests/test_config.py -v`　预期全 pass。
```bash
git add pyproject.toml uv.lock src/harness/config.py tests/test_config.py
git commit -m "chore: 沙箱/HTTP 依赖与配置项"
```

---

## 任务 1：Sandbox 协议 + 路径约束 `sandbox/base.py`

**文件：** 创建 `src/harness/sandbox/__init__.py`、`src/harness/sandbox/base.py`、测试 `tests/test_sandbox_base.py`

- [ ] **步骤 1：建包**　运行：`touch src/harness/sandbox/__init__.py`

- [ ] **步骤 2：写失败测试**

```python
# tests/test_sandbox_base.py
import os
import pytest
from harness.sandbox.base import resolve_in_workspace, SandboxError, ExecResult


def test_resolve_ok(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    p = resolve_in_workspace(str(ws), "sub/file.txt")
    assert p == os.path.join(os.path.realpath(str(ws)), "sub", "file.txt")


def test_resolve_dot_is_workspace(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    assert resolve_in_workspace(str(ws), ".") == os.path.realpath(str(ws))


def test_resolve_relative_escape(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    with pytest.raises(SandboxError):
        resolve_in_workspace(str(ws), "../../etc/passwd")


def test_resolve_absolute_escape(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    with pytest.raises(SandboxError):
        resolve_in_workspace(str(ws), "/etc/passwd")


def test_resolve_symlink_escape(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    secret = tmp_path / "secret.txt"; secret.write_text("x")
    (ws / "link").symlink_to(secret)
    with pytest.raises(SandboxError):
        resolve_in_workspace(str(ws), "link")


def test_exec_result_defaults():
    r = ExecResult("o", "e", 0)
    assert r.timed_out is False
```

运行：预期 FAIL。

- [ ] **步骤 3：实现 `src/harness/sandbox/base.py`**

```python
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
```

- [ ] **步骤 4：跑通并 commit**

运行：`uv run pytest tests/test_sandbox_base.py -v`　预期：6 passed。
```bash
git add src/harness/sandbox/__init__.py src/harness/sandbox/base.py tests/test_sandbox_base.py
git commit -m "feat: Sandbox 协议 + 路径约束"
```

---

## 任务 2：LocalSandbox `sandbox/local.py`

**文件：** 创建 `src/harness/sandbox/local.py`、测试 `tests/test_local_sandbox.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/test_local_sandbox.py
import pytest
from harness.sandbox.local import LocalSandbox
from harness.sandbox.base import SandboxError


async def test_exec_and_file_roundtrip():
    sb = LocalSandbox()
    await sb.start()
    try:
        r = await sb.exec(["echo", "hi"], timeout=5)
        assert r.exit_code == 0 and "hi" in r.stdout and r.timed_out is False
        await sb.write_file("a.txt", "hello")
        assert await sb.read_file("a.txt") == "hello"
        assert "a.txt" in await sb.list_files(".")
    finally:
        await sb.close()


async def test_exec_shell_via_sh_c():
    sb = LocalSandbox()
    await sb.start()
    try:
        r = await sb.exec(["sh", "-c", "echo $((1+1))"], timeout=5)
        assert "2" in r.stdout
    finally:
        await sb.close()


async def test_timeout_kills():
    sb = LocalSandbox()
    await sb.start()
    try:
        r = await sb.exec(["sleep", "5"], timeout=0.3)
        assert r.timed_out is True
    finally:
        await sb.close()


async def test_path_escape_rejected():
    sb = LocalSandbox()
    await sb.start()
    try:
        with pytest.raises(SandboxError):
            await sb.read_file("../../etc/passwd")
    finally:
        await sb.close()
```

运行：预期 FAIL。

- [ ] **步骤 2：实现 `src/harness/sandbox/local.py`**

```python
# src/harness/sandbox/local.py
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile

from .base import ExecResult, resolve_in_workspace


class LocalSandbox:
    """本地临时目录 + 子进程。仅测试/离线开发用——不是安全边界。"""

    def __init__(self) -> None:
        self.workspace = ""
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self.workspace = tempfile.mkdtemp(prefix="harness_sbx_")
        self._started = True

    async def close(self) -> None:
        if self._started and self.workspace and os.path.isdir(self.workspace):
            shutil.rmtree(self.workspace, ignore_errors=True)
        self._started = False

    async def exec(self, command: list[str], timeout: float) -> ExecResult:
        await self.start()
        proc = await asyncio.create_subprocess_exec(
            *command, cwd=self.workspace,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return ExecResult(out.decode(errors="replace"), err.decode(errors="replace"),
                              proc.returncode, timed_out=False)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ExecResult("", f"超时（>{timeout}s）被终止", -1, timed_out=True)

    async def write_file(self, path: str, content: str) -> None:
        await self.start()
        real = resolve_in_workspace(self.workspace, path)
        os.makedirs(os.path.dirname(real), exist_ok=True)
        with open(real, "w") as f:
            f.write(content)

    async def read_file(self, path: str) -> str:
        await self.start()
        real = resolve_in_workspace(self.workspace, path)
        with open(real) as f:
            return f.read()

    async def list_files(self, path: str = ".") -> list[str]:
        await self.start()
        real = resolve_in_workspace(self.workspace, path)
        return sorted(os.listdir(real))
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/test_local_sandbox.py -v`　预期：4 passed。
```bash
git add src/harness/sandbox/local.py tests/test_local_sandbox.py
git commit -m "feat: LocalSandbox（测试/离线，非安全边界）"
```

---

## 任务 3：网络策略 `net/policy.py`

**文件：** 创建 `src/harness/net/__init__.py`、`src/harness/net/policy.py`、测试 `tests/test_net_policy.py`

- [ ] **步骤 1：建包**　运行：`touch src/harness/net/__init__.py`

- [ ] **步骤 2：写失败测试**

```python
# tests/test_net_policy.py
import pytest
from harness.net.policy import check_url, PolicyError


def _to(ip):
    return lambda host: [ip]


def test_block_loopback():
    with pytest.raises(PolicyError):
        check_url("http://x/", [], True, resolve=_to("127.0.0.1"))


def test_block_metadata():
    with pytest.raises(PolicyError):
        check_url("http://x/", [], True, resolve=_to("169.254.169.254"))


def test_block_private():
    with pytest.raises(PolicyError):
        check_url("http://x/", [], True, resolve=_to("10.0.0.5"))


def test_public_allowed():
    check_url("http://x/", [], True, resolve=_to("93.184.216.34"))  # 不抛


def test_allowlist_reject_and_accept():
    with pytest.raises(PolicyError):
        check_url("http://evil.com/", ["example.com"], False, resolve=_to("1.2.3.4"))
    check_url("http://api.example.com/", ["example.com"], False, resolve=_to("1.2.3.4"))


def test_scheme_rejected():
    with pytest.raises(PolicyError):
        check_url("file:///etc/passwd", [], True)
```

运行：预期 FAIL。

- [ ] **步骤 3：实现 `src/harness/net/policy.py`**

```python
# src/harness/net/policy.py
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class PolicyError(Exception):
    ...


def default_resolve(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None)
    return list({info[4][0] for info in infos})


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # 无法识别的地址一律视为不安全
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _host_allowed(host: str, allowed_domains: list[str]) -> bool:
    host = host.lower()
    for d in allowed_domains:
        d = d.lower()
        if host == d or host.endswith("." + d):
            return True
    return False


def check_url(url: str, allowed_domains: list[str], block_private: bool,
              resolve=default_resolve) -> None:
    """URL 不合规则抛 PolicyError。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise PolicyError(f"仅允许 http/https：{parsed.scheme or '(空)'}")
    host = parsed.hostname
    if not host:
        raise PolicyError(f"无效 URL：{url}")
    if allowed_domains and not _host_allowed(host, allowed_domains):
        raise PolicyError(f"域名不在白名单：{host}")
    if block_private:
        ips = resolve(host)
        if not ips:
            raise PolicyError(f"无法解析主机：{host}")
        for ip in ips:
            if _is_blocked_ip(ip):
                raise PolicyError(f"目标为内网/保留地址，已拦截：{host} → {ip}")
```

- [ ] **步骤 4：跑通并 commit**

运行：`uv run pytest tests/test_net_policy.py -v`　预期：6 passed。
```bash
git add src/harness/net/__init__.py src/harness/net/policy.py tests/test_net_policy.py
git commit -m "feat: net 策略（白名单 + SSRF 拦截）"
```

---

## 任务 4：容器工具（文件/shell/代码）+ 辅助

**文件：** 创建 `src/harness/tools/builtins/_sandbox_util.py`、`fs_tools.py`、`shell_tool.py`、`code_tool.py`、测试 `tests/test_sandbox_tools.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/test_sandbox_tools.py
import pytest
from harness.sandbox.local import LocalSandbox
from harness.tools.base import ToolRegistry, ToolExecutor
from harness.tools.builtins.fs_tools import WriteFileTool, ReadFileTool, ListFilesTool
from harness.tools.builtins.shell_tool import RunShellTool
from harness.tools.builtins.code_tool import RunPythonTool
from harness.types import ToolCall


async def _executor(sb):
    reg = ToolRegistry()
    reg.register(WriteFileTool(sb))
    reg.register(ReadFileTool(sb, max_chars=8000))
    reg.register(ListFilesTool(sb))
    reg.register(RunShellTool(sb, timeout=5, max_chars=8000))
    reg.register(RunPythonTool(sb, timeout=5, max_chars=8000))
    return ToolExecutor(reg)


async def test_write_read_list_roundtrip():
    sb = LocalSandbox(); await sb.start()
    try:
        ex = await _executor(sb)
        w = await ex.execute(ToolCall(id="c1", name="write_file",
                                      arguments={"path": "note.txt", "content": "hi"}))
        assert w.is_error is False
        r = await ex.execute(ToolCall(id="c2", name="read_file", arguments={"path": "note.txt"}))
        assert r.content == "hi"
        ls = await ex.execute(ToolCall(id="c3", name="list_files", arguments={"path": "."}))
        assert "note.txt" in ls.content
    finally:
        await sb.close()


async def test_run_python():
    sb = LocalSandbox(); await sb.start()
    try:
        ex = await _executor(sb)
        r = await ex.execute(ToolCall(id="c1", name="run_python",
                                      arguments={"code": "print(1+1)"}))
        assert "2" in r.content and r.is_error is False
    finally:
        await sb.close()


async def test_run_shell():
    sb = LocalSandbox(); await sb.start()
    try:
        ex = await _executor(sb)
        r = await ex.execute(ToolCall(id="c1", name="run_shell",
                                      arguments={"command": "echo abc"}))
        assert "abc" in r.content
    finally:
        await sb.close()


async def test_path_escape_is_error():
    sb = LocalSandbox(); await sb.start()
    try:
        ex = await _executor(sb)
        r = await ex.execute(ToolCall(id="c1", name="read_file",
                                      arguments={"path": "../../etc/passwd"}))
        assert r.is_error is True   # SandboxError 经 ToolExecutor 兜成 is_error
    finally:
        await sb.close()
```

运行：预期 FAIL。

- [ ] **步骤 2：实现辅助 `src/harness/tools/builtins/_sandbox_util.py`**

```python
# src/harness/tools/builtins/_sandbox_util.py
from __future__ import annotations

from ...sandbox.base import ExecResult


def truncate(text: str, max_chars: int) -> str:
    if max_chars and len(text) > max_chars:
        return text[:max_chars] + "…(已截断)"
    return text


def format_exec(res: ExecResult, max_chars: int) -> str:
    header = f"exit_code={res.exit_code}" + ("（超时）" if res.timed_out else "")
    parts = [header]
    if res.stdout:
        parts.append("stdout:\n" + res.stdout)
    if res.stderr:
        parts.append("stderr:\n" + res.stderr)
    return truncate("\n".join(parts), max_chars)
```

- [ ] **步骤 3：实现 `src/harness/tools/builtins/fs_tools.py`**

```python
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
```

- [ ] **步骤 4：实现 `src/harness/tools/builtins/shell_tool.py`**

```python
# src/harness/tools/builtins/shell_tool.py
from __future__ import annotations

from pydantic import BaseModel

from ..base import Tool
from ...sandbox.base import Sandbox
from ._sandbox_util import format_exec


class RunShellTool(Tool):
    name = "run_shell"
    description = "在沙箱内执行 shell 命令，返回 stdout/stderr/退出码。"

    class Params(BaseModel):
        command: str

    def __init__(self, sandbox: Sandbox, timeout: float = 30.0, max_chars: int = 8000) -> None:
        self._sandbox = sandbox
        self._timeout = timeout
        self._max_chars = max_chars

    async def run(self, params: "RunShellTool.Params") -> str:
        res = await self._sandbox.exec(["sh", "-c", params.command], self._timeout)
        return format_exec(res, self._max_chars)
```

- [ ] **步骤 5：实现 `src/harness/tools/builtins/code_tool.py`**

```python
# src/harness/tools/builtins/code_tool.py
from __future__ import annotations

from pydantic import BaseModel

from ..base import Tool
from ...sandbox.base import Sandbox
from ._sandbox_util import format_exec


class RunPythonTool(Tool):
    name = "run_python"
    description = "在沙箱内执行 Python 代码，返回输出。"

    class Params(BaseModel):
        code: str

    def __init__(self, sandbox: Sandbox, timeout: float = 30.0,
                 max_chars: int = 8000, python_cmd: str = "python3") -> None:
        self._sandbox = sandbox
        self._timeout = timeout
        self._max_chars = max_chars
        self._python_cmd = python_cmd

    async def run(self, params: "RunPythonTool.Params") -> str:
        await self._sandbox.write_file("_run.py", params.code)   # SandboxError→is_error
        res = await self._sandbox.exec([self._python_cmd, "_run.py"], self._timeout)
        return format_exec(res, self._max_chars)
```

- [ ] **步骤 6：跑通并 commit**

运行：`uv run pytest tests/test_sandbox_tools.py -v`　预期：4 passed。
```bash
git add src/harness/tools/builtins/_sandbox_util.py src/harness/tools/builtins/fs_tools.py src/harness/tools/builtins/shell_tool.py src/harness/tools/builtins/code_tool.py tests/test_sandbox_tools.py
git commit -m "feat: 文件/shell/代码 沙箱工具"
```

---

## 任务 5：外部 API/HTTP 工具 `http_tool.py`

**文件：** 创建 `src/harness/tools/builtins/http_tool.py`、测试 `tests/test_http_tool.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/test_http_tool.py
import httpx
import pytest

from harness.tools.base import ToolRegistry, ToolExecutor
from harness.tools.builtins.http_tool import HttpRequestTool
from harness.types import ToolCall


def _factory(handler):
    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


def _public(host):
    return ["93.184.216.34"]


async def test_success_returns_body():
    def handler(req):
        return httpx.Response(200, text="hello world")
    tool = HttpRequestTool([], True, 5.0, 1000, 3, client_factory=_factory(handler), resolve=_public)
    out = await tool.run(tool.Params(url="http://example.com/"))
    assert "200" in out and "hello world" in out


async def test_ssrf_blocked_is_error():
    def handler(req):
        return httpx.Response(200, text="secret")
    tool = HttpRequestTool([], True, 5.0, 1000, 3,
                           client_factory=_factory(handler), resolve=lambda h: ["127.0.0.1"])
    reg = ToolRegistry(); reg.register(tool)
    ex = ToolExecutor(reg)
    r = await ex.execute(ToolCall(id="c1", name="http_request",
                                  arguments={"url": "http://internal/"}))
    assert r.is_error is True


async def test_response_truncated():
    def handler(req):
        return httpx.Response(200, text="A" * 5000)
    tool = HttpRequestTool([], True, 5.0, 100, 3, client_factory=_factory(handler), resolve=_public)
    out = await tool.run(tool.Params(url="http://example.com/"))
    assert "…(已截断)" in out


async def test_redirect_to_internal_blocked():
    def handler(req):
        if req.url.host == "example.com":
            return httpx.Response(302, headers={"location": "http://internal/"})
        return httpx.Response(200, text="internal")
    # example.com 公网、internal 解析到内网
    def resolve(host):
        return ["93.184.216.34"] if host == "example.com" else ["127.0.0.1"]
    tool = HttpRequestTool([], True, 5.0, 1000, 3, client_factory=_factory(handler), resolve=resolve)
    reg = ToolRegistry(); reg.register(tool)
    ex = ToolExecutor(reg)
    r = await ex.execute(ToolCall(id="c1", name="http_request",
                                  arguments={"url": "http://example.com/"}))
    assert r.is_error is True   # 第二跳内网被策略拦截
```

运行：预期 FAIL。

- [ ] **步骤 2：实现 `src/harness/tools/builtins/http_tool.py`**

```python
# src/harness/tools/builtins/http_tool.py
from __future__ import annotations

import httpx
from pydantic import BaseModel

from ..base import Tool
from ...net.policy import check_url


class HttpRequestTool(Tool):
    name = "http_request"
    description = "发起 HTTP(S) 请求抓取网页或调用外部 API。默认可访问公网，禁止内网地址。"

    class Params(BaseModel):
        url: str
        method: str = "GET"
        headers: dict | None = None
        body: str | None = None

    def __init__(self, allowed_domains, block_private: bool = True, timeout: float = 30.0,
                 max_bytes: int = 5_000_000, max_redirects: int = 5,
                 client_factory=None, resolve=None) -> None:
        self._allowed = allowed_domains
        self._block_private = block_private
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(follow_redirects=False, timeout=timeout))
        self._resolve_kw = {"resolve": resolve} if resolve is not None else {}

    async def run(self, params: "HttpRequestTool.Params") -> str:
        url = params.url
        async with self._client_factory() as client:
            for _ in range(self._max_redirects + 1):
                check_url(url, self._allowed, self._block_private, **self._resolve_kw)  # PolicyError→is_error
                resp = await client.request(params.method, url,
                                            headers=params.headers, content=params.body)
                if resp.is_redirect and "location" in resp.headers:
                    url = str(httpx.URL(url).join(resp.headers["location"]))
                    continue
                body = resp.text
                suffix = "…(已截断)" if len(body) > self._max_bytes else ""
                return f"HTTP {resp.status_code}\n{body[: self._max_bytes]}{suffix}"
        raise RuntimeError(f"超过最大重定向次数（{self._max_redirects}）")
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/test_http_tool.py -v`　预期：4 passed。
```bash
git add src/harness/tools/builtins/http_tool.py tests/test_http_tool.py
git commit -m "feat: http_request 工具（SSRF 防护 + 白名单 + 重定向逐跳校验）"
```

---

## 任务 6：DockerSandbox + 可跳过集成测试

**文件：** 创建 `src/harness/sandbox/docker.py`、`src/harness/sandbox/factory.py`、测试 `tests/test_docker_sandbox.py`

- [ ] **步骤 1：实现 `src/harness/sandbox/docker.py`**（生产实现；无本地 docker 时由步骤 3 的 skip 测试覆盖）

```python
# src/harness/sandbox/docker.py
from __future__ import annotations

import io
import os
import tarfile

from .base import ExecResult, resolve_in_workspace


class DockerSandbox:
    """远程 Linux 云服务器的 Docker 容器沙箱（docker SDK over SSH）。真正的安全边界。"""

    def __init__(self, docker_host: str, image: str, workspace: str = "/workspace",
                 user: str = "1000:1000", network: str = "none", mem_limit: str = "512m",
                 cpus: float = 1.0, pids_limit: int = 128) -> None:
        self.workspace = workspace
        self._docker_host = docker_host
        self._image = image
        self._user = user
        self._network = network
        self._mem_limit = mem_limit
        self._cpus = cpus
        self._pids_limit = pids_limit
        self._client = None
        self._container = None

    async def start(self) -> None:
        if self._container is not None:
            return
        import docker
        self._client = docker.DockerClient(base_url=self._docker_host)
        self._container = self._client.containers.run(
            self._image, command="sleep infinity", detach=True,
            working_dir=self.workspace, user=self._user, network_mode=self._network,
            read_only=True, tmpfs={self.workspace: "rw,size=64m"},
            mem_limit=self._mem_limit, nano_cpus=int(self._cpus * 1e9),
            pids_limit=self._pids_limit, cap_drop=["ALL"],
            security_opt=["no-new-privileges"], auto_remove=False)

    async def close(self) -> None:
        if self._container is not None:
            self._container.remove(force=True)
            self._container = None
        if self._client is not None:
            self._client.close()
            self._client = None

    async def exec(self, command: list[str], timeout: float) -> ExecResult:
        await self.start()
        wrapped = ["timeout", str(int(timeout)), *command]
        res = self._container.exec_run(wrapped, workdir=self.workspace, demux=True)
        out, err = res.output if isinstance(res.output, tuple) else (res.output, b"")
        return ExecResult((out or b"").decode(errors="replace"),
                          (err or b"").decode(errors="replace"),
                          res.exit_code, timed_out=(res.exit_code == 124))

    async def write_file(self, path: str, content: str) -> None:
        await self.start()
        real = resolve_in_workspace(self.workspace, path)
        data = content.encode()
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as tar:
            info = tarfile.TarInfo(name=os.path.basename(real))
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        self._container.put_archive(os.path.dirname(real), stream.getvalue())

    async def read_file(self, path: str) -> str:
        await self.start()
        real = resolve_in_workspace(self.workspace, path)
        bits, _ = self._container.get_archive(real)
        stream = io.BytesIO(b"".join(bits))
        with tarfile.open(fileobj=stream) as tar:
            member = tar.next()
            return tar.extractfile(member).read().decode(errors="replace")

    async def list_files(self, path: str = ".") -> list[str]:
        res = await self.exec(["ls", "-1", path], timeout=10)
        return [ln for ln in res.stdout.splitlines() if ln]
```

- [ ] **步骤 2：实现 `src/harness/sandbox/factory.py`**

```python
# src/harness/sandbox/factory.py
from __future__ import annotations

from .local import LocalSandbox


def build_sandbox(config):
    """按 config.sandbox_backend 造 Sandbox 实例。"""
    if config.sandbox_backend == "docker":
        from .docker import DockerSandbox
        return DockerSandbox(
            docker_host=config.sandbox_docker_host, image=config.sandbox_image,
            workspace=config.sandbox_workspace, user=config.sandbox_user,
            network=config.sandbox_network, mem_limit=config.sandbox_mem_limit,
            cpus=config.sandbox_cpus, pids_limit=config.sandbox_pids_limit)
    return LocalSandbox()
```

- [ ] **步骤 3：写 factory 单测 + Docker 可跳过集成测试**

```python
# tests/test_docker_sandbox.py
import os
import pytest

from harness.config import HarnessConfig
from harness.sandbox.factory import build_sandbox
from harness.sandbox.local import LocalSandbox


def test_factory_local_default():
    cfg = HarnessConfig(api_key="k")
    assert isinstance(build_sandbox(cfg), LocalSandbox)


@pytest.mark.skipif(not os.getenv("HARNESS_SANDBOX_DOCKER_HOST"),
                    reason="需要真实远程 docker（HARNESS_SANDBOX_DOCKER_HOST）")
async def test_docker_exec_roundtrip():
    cfg = HarnessConfig(api_key="k", sandbox_backend="docker",
                        sandbox_docker_host=os.environ["HARNESS_SANDBOX_DOCKER_HOST"])
    sb = build_sandbox(cfg)
    await sb.start()
    try:
        await sb.write_file("a.txt", "hi")
        assert await sb.read_file("a.txt") == "hi"
        r = await sb.exec(["python3", "-c", "print(6*7)"], timeout=15)
        assert "42" in r.stdout
    finally:
        await sb.close()
```

- [ ] **步骤 4：跑通并 commit**

运行：`uv run pytest tests/test_docker_sandbox.py -v`　预期：1 passed, 1 skipped（无远程 docker）。
```bash
git add src/harness/sandbox/docker.py src/harness/sandbox/factory.py tests/test_docker_sandbox.py
git commit -m "feat: DockerSandbox（远程容器）+ factory + 可跳过集成测试"
```

---

## 任务 7：端到端集成 + demo

**文件：** 测试 `tests/test_sandbox_integration.py`、新增 `examples/sandbox_demo.py`

- [ ] **步骤 1：写集成测试**（agent loop 中调用 run_python，mock 模型 + LocalSandbox）

```python
# tests/test_sandbox_integration.py
from harness.loop.agent_loop import AgentLoop
from harness.context.manager import ContextManager
from harness.tools.base import ToolRegistry
from harness.sandbox.local import LocalSandbox
from harness.tools.builtins.code_tool import RunPythonTool
from harness.tools.builtins.fs_tools import WriteFileTool
from harness.llm.base import StreamChunk, ToolCallDelta
from harness.events import ToolFinished, RunFinished


async def test_agent_runs_code_in_sandbox(make_mock, text_turn):
    sb = LocalSandbox()
    await sb.start()
    try:
        reg = ToolRegistry()
        reg.register(RunPythonTool(sb, timeout=5))
        reg.register(WriteFileTool(sb))
        code_turn = [
            StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
                index=0, id="c1", name="run_python",
                arguments='{"code": "print(6*7)"}')),
            StreamChunk(type="done"),
        ]
        loop = AgentLoop(client=make_mock([code_turn, text_turn("答案是 42")]),
                         registry=reg, context=ContextManager(system_prompt="s"),
                         max_steps=5, run_id_factory=lambda: "r1")
        events = [e async for e in loop.run("算 6*7")]
        finished = [e for e in events if isinstance(e, ToolFinished)]
        assert "42" in finished[0].result.content
        assert finished[0].result.is_error is False
        assert isinstance(events[-1], RunFinished)
    finally:
        await sb.close()
```

运行：`uv run pytest tests/test_sandbox_integration.py -v`　预期：1 passed。

- [ ] **步骤 2：新增 `examples/sandbox_demo.py`**（组装接线，供手动验收）

```python
# examples/sandbox_demo.py
"""沙箱 + HTTP 工具手动验收。默认 LocalSandbox（sandbox_backend=local）。
切远程容器：在 .env 设 HARNESS_SANDBOX_BACKEND=docker + HARNESS_SANDBOX_DOCKER_HOST=ssh://user@host。

运行：uv run python examples/sandbox_demo.py "用 python 算 12 的阶乘"
"""
from __future__ import annotations

import asyncio
import sys

from harness.config import HarnessConfig
from harness.context.manager import ContextManager
from harness.llm.openai_compat import OpenAICompatibleClient
from harness.loop.agent_loop import AgentLoop
from harness.sandbox.factory import build_sandbox
from harness.tools.base import ToolRegistry
from harness.tools.builtins.fs_tools import WriteFileTool, ReadFileTool, ListFilesTool
from harness.tools.builtins.shell_tool import RunShellTool
from harness.tools.builtins.code_tool import RunPythonTool
from harness.tools.builtins.http_tool import HttpRequestTool
from harness.events import TextDelta, ToolStarted, ToolFinished, RunFinished, RunError


async def main(msg: str) -> None:
    cfg = HarnessConfig()
    sb = build_sandbox(cfg)
    reg = ToolRegistry()
    reg.register(WriteFileTool(sb))
    reg.register(ReadFileTool(sb, cfg.sandbox_output_max_chars))
    reg.register(ListFilesTool(sb))
    reg.register(RunShellTool(sb, cfg.sandbox_exec_timeout, cfg.sandbox_output_max_chars))
    reg.register(RunPythonTool(sb, cfg.sandbox_exec_timeout, cfg.sandbox_output_max_chars))
    reg.register(HttpRequestTool(cfg.http_allowed_domains, cfg.http_block_private,
                                 cfg.http_timeout, cfg.http_max_response_bytes,
                                 cfg.http_max_redirects))
    loop = AgentLoop(
        client=OpenAICompatibleClient(cfg), registry=reg,
        context=ContextManager(system_prompt="你可以用沙箱工具执行代码/命令/读写文件、用 http_request 抓取网页。"),
        max_steps=cfg.max_steps, model_name=cfg.model)
    try:
        async for ev in loop.run(msg):
            if isinstance(ev, TextDelta):
                print(ev.text, end="", flush=True)
            elif isinstance(ev, ToolStarted):
                print(f"\n[工具] {ev.tool_call.name} {ev.tool_call.arguments}")
            elif isinstance(ev, ToolFinished):
                print(f"[结果] {ev.result.content[:200]}")
            elif isinstance(ev, RunFinished):
                print(f"\n\n[完成] {ev.message.content}")
            elif isinstance(ev, RunError):
                print(f"\n\n[出错] {ev.error}")
    finally:
        await sb.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "用 python 算 12 的阶乘"))
```

- [ ] **步骤 3：全量测试并 commit**

运行：`uv run pytest -q`　预期：全绿（`test_integration_real` + docker 集成 均 skipped）。
```bash
git add tests/test_sandbox_integration.py examples/sandbox_demo.py
git commit -m "feat: 沙箱端到端集成测试 + demo"
```

---

## 完成标准（对照规格验收）

- [ ] `LocalSandbox` 下 `run_python("print(1+1)")` 含 `2`；write→read→list 工作区共享（任务 2/4）
- [ ] 路径逃逸被拒为 `is_error`（任务 1/4）
- [ ] `exec` 超时被 kill、`timed_out=True`（任务 2）
- [ ] 输出超限截断（任务 4 辅助）
- [ ] 容器工具在 agent loop 被调用、回填、作答（任务 7）
- [ ] HTTP：SSRF/白名单拒为 `is_error`、重定向逐跳校验、超大响应截断（任务 5）
- [ ] `DockerSandbox` 有可跳过集成测试（任务 6）
- [ ] ①②③a 原有测试无回归（`uv run pytest` 全绿）
```

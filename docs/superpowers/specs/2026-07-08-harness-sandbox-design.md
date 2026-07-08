# AI Harness 沙箱执行（子项目③b-1）设计规格

- **日期**：2026-07-08
- **状态**：待实现（brainstorming 已定稿）
- **定位**：单用户自用工具，在①内核 + ②可靠性 + ③a 记忆之上扩展
- **前置**：①②③a 已完成并在 `main`

---

## 0. 背景与范围

子项目③b（沙箱执行）拆成：**③b-1 `Sandbox` 隔离原语 + 文件/shell/代码工具**（本规格）、③b-2 浏览器工具、③b-3 外部 API/联网工具。本规格只覆盖 ③b-1。

**隔离机制（用户决定）**：用**远程 Linux 云服务器的 Docker 容器**做沙箱，harness 经 **docker SDK over SSH**（`DOCKER_HOST=ssh://user@host`）驱动。每 session 惰性建一个临时容器、结束销毁。

**存储/可观测性**：沿用 SQLite+sqlite-vec / OpenTelemetry（工具执行自动进②的 `tool_call` span）。

设计通则：严格 YAGNI；测试用 `LocalSandbox` 不碰云/Docker；安全默认（禁网、非 root、仅工作区可写、资源上限、超时）；不回归①②③a。

---

## 1. 范围与验收

### IN
`Sandbox` 隔离原语 + 基于它的文件/shell/代码工具（文件含读/写/列 3 个，加 shell、代码，共 5 个工具类）。
- `Sandbox` 协议：`start`/`close`（生命周期）、`exec`、`write_file`/`read_file`/`list_files`。
- `DockerSandbox`（生产）：docker SDK over SSH，每 session 惰性建临时容器，隔离策略，结束销毁。
- `LocalSandbox`（测试/离线）：本地临时工作区 + 子进程 + 超时 + 路径约束。**仅测试用，非安全边界。**
- 三工具：`write_file`/`read_file`/`list_files`、`run_shell`、`run_python`，在沙箱工作区内、路径受约束。

### OUT（后续/预留）
浏览器工具（③b-2）、外部 API/联网工具（③b-3）、容器内联网与 pip（默认禁网，留开关）、并发容器池、非 Python 语言执行。

### 验收标准
1. `LocalSandbox` 下 `run_python("print(1+1)")` 返回含 `2`；`write_file` 后 `read_file`/`list_files` 可见（工作区共享）。
2. 路径逃逸（`../../etc/passwd`、绝对路径、符号链接）被拒（`is_error`，不越界）。
3. `run_shell("sleep 999")` 在 `sandbox_exec_timeout` 后被终止、`timed_out=True`、返回超时错误（不挂死）。
4. 输出超 `sandbox_output_max_chars` 截断。
5. 三工具在 agent loop 被调用、结果回填、模型据此继续（mock 模型 + `LocalSandbox`）。
6. `DockerSandbox` 有 1 个可跳过集成测试（无 `sandbox_docker_host` 则 skip）。
7. ①②③a 原有测试不回归。

---

## 2. 架构与模块

**设计取向**：`Sandbox` 是协议（仿 `ModelClient`/`EmbeddingClient`），三工具只依赖协议——生产注入 `DockerSandbox`、测试注入 `LocalSandbox`。session 内三工具共享**同一 Sandbox 实例**（同一容器/工作区）。工具执行自动走②的 `tool_call` span / 错误回填 / 截断。

```
src/harness/sandbox/           [新增]
├── __init__.py
├── base.py         Sandbox 协议 + ExecResult + SandboxError + resolve_in_workspace()
├── local.py        LocalSandbox（临时目录 + 子进程；测试/离线）
└── docker.py       DockerSandbox（docker SDK over SSH；远程临时容器 + 隔离策略）
src/harness/tools/builtins/    [新增]
├── fs_tools.py     WriteFileTool / ReadFileTool / ListFilesTool
├── shell_tool.py   RunShellTool
└── code_tool.py    RunPythonTool
src/harness/config.py          [改] docker host/image/工作区/资源上限/超时/网络等
```

**依赖新增**：`docker[ssh]`（Python SDK + SSH 传输的 paramiko）。`LocalSandbox` 仅标准库。

**边界**：
- `Sandbox` 协议——测试用 `LocalSandbox`，不碰云/Docker。
- `DockerSandbox`/`LocalSandbox` 只认"隔离环境里执行命令/读写文件"，不认 LLM/工具。
- 三工具持有 `Sandbox` 引用，与①`CalculatorTool` 同构。
- **安全边界说明**：真正隔离来自 `DockerSandbox`（远程容器+策略）；`LocalSandbox` 仅软约束、仅测试用，绝不当生产隔离。

---

## 3. Sandbox 协议 + 路径约束

```python
@dataclass
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False

class SandboxError(Exception): ...

class Sandbox(Protocol):
    workspace: str
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def exec(self, command: list[str], timeout: float) -> ExecResult: ...
    async def write_file(self, path: str, content: str) -> None: ...
    async def read_file(self, path: str) -> str: ...
    async def list_files(self, path: str = ".") -> list[str]: ...
```

**路径约束**（`base.py::resolve_in_workspace(workspace, path)`）：把相对/绝对路径规约到工作区内，**任何逃出工作区的路径（`../`、绝对路径、符号链接逃逸）抛 `SandboxError`**。两实现共用，保证一致。命令用 `list[str]`（不经 shell）避免注入；`run_shell` 是显式例外（走 `sh -c`）。

---

## 4. DockerSandbox（生产）

Python `docker` SDK，`base_url` 走 `ssh://user@host`（来自 `sandbox_docker_host` 或 `DOCKER_HOST`）。

- **生命周期**：`start()` 惰性 `client.containers.run(image, command="sleep infinity", detach=True, **策略)` 建临时容器；文件/命令走 `container.exec_run`/`put_archive`/`get_archive`（tar 归档，无 shell 转义）；`close()` `container.remove(force=True)`。
- **隔离策略**（`containers.run` 参数，安全默认、可配置）：
  - `network_mode="none"`（默认禁网）、`user="1000:1000"`（非 root）、`read_only=True` + 工作区 `tmpfs`/卷可写、`mem_limit`、`nano_cpus`、`pids_limit`、`cap_drop=["ALL"]`、`security_opt=["no-new-privileges"]`、无 host bind 挂载。
- **超时**：`exec` 用 `timeout` 命令包裹或客户端侧计时；超时 → `timed_out=True`。
- **OTel**：容器创建 / 每次 exec 打 span（镜像、exit_code、耗时、timed_out）。

---

## 5. LocalSandbox（测试/离线）

- `start()` 建 `tempfile.mkdtemp()` 工作区；`close()` 删目录。
- `exec` 用 `asyncio.create_subprocess_exec`（**非 shell**）在工作区、带 `timeout`（超时 `kill()` + `timed_out=True`）；`run_shell` 场景走 `create_subprocess_shell`。
- `write_file`/`read_file`/`list_files` 经 `resolve_in_workspace` 约束在临时目录内。
- **定位**：软约束、仅测试与离线开发，**非安全边界**（docstring 显著标注）。

---

## 6. 三个工具

都持有 `Sandbox` 引用，与①`CalculatorTool` 同构；结果含 stdout/stderr/exit_code、按 `sandbox_output_max_chars` 截断：

```python
class WriteFileTool(Tool):  name="write_file"  Params(path:str, content:str)
class ReadFileTool(Tool):   name="read_file"   Params(path:str)
class ListFilesTool(Tool):  name="list_files"  Params(path:str=".")
class RunShellTool(Tool):   name="run_shell"   Params(command:str)   # sandbox.exec(["sh","-c",cmd])
class RunPythonTool(Tool):  name="run_python"  Params(code:str)      # 写 workspace 临时 .py → exec(["python", f])
```

- 每个 `run` 调对应 `Sandbox` 方法；`SandboxError`/超时/非零退出码都兜成**可读 `is_error` 结果回填**（自动走②自纠正）。
- 路径逃逸尝试 → `is_error` + 明确提示。
- 装配：同一个 `Sandbox` 实例注入五个工具、`registry.register(...)`；`Sandbox.start()` 惰性（首次工具调用时）。

---

## 7. 配置 / 测试 / 依赖

### 新增配置（`config.py`，安全默认；默认 backend=local 不碰云）
```
sandbox_backend: str = "local"          # local | docker
sandbox_docker_host: str = ""           # ssh://user@host
sandbox_image: str = "python:3.12-slim"
sandbox_workspace: str = "/workspace"   # 容器内工作区（local 用临时目录）
sandbox_user: str = "1000:1000"         # 非 root uid:gid
sandbox_network: str = "none"
sandbox_mem_limit: str = "512m"
sandbox_cpus: float = 1.0               # → nano_cpus
sandbox_pids_limit: int = 128
sandbox_exec_timeout: float = 30.0
sandbox_output_max_chars: int = 8000
```

### 测试策略（`LocalSandbox` 全覆盖、不碰云/Docker）
- `resolve_in_workspace` 单测：`../`、绝对路径、符号链接逃逸都抛 `SandboxError`。
- `LocalSandbox`：`start/close` 临时目录；`exec` echo；`write/read/list` 往返共享工作区；超时被 kill、`timed_out=True`。
- 三工具经 `ToolExecutor`：`write_file`→`read_file` 往返；`run_python("print(1+1)")`→含 `2`；逃逸路径 → `is_error`；输出截断。
- 集成：`AgentLoop` + mock 模型（发 `run_python`/`write_file`）+ `LocalSandbox` → 断言回填并被作答引用。
- `DockerSandbox`：1 个 `@pytest.mark.skipif(not sandbox_docker_host)` 集成测试。

### 依赖新增
`docker[ssh]`（Python SDK + paramiko）。`LocalSandbox` 仅标准库。

---

## 8. 后续衔接（备忘，非本次范围）

- **③b-2 浏览器工具**：Playwright headless 抓网页（可跑在同一沙箱容器或独立）。
- **③b-3 外部 API/联网工具**：HTTP + 域名白名单 + 容器网络开关（放开 `sandbox_network`）。
- **③c 多 Agent 编排**、**③d 持久化**。
- App 层：代码执行/文件工具支撑"执行代码或脚本""整理成图"等学习助手功能。

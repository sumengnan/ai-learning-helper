> ⚠️ **历史设计记录（已过时）**：harness 内核已抽成外部包
> [ai-harness-framework](https://github.com/sumengnan/ai-harness-framework)（import 名仍是 `harness`）。
> 本文是带日期的设计存档，文中的 `src/harness/` 路径与打包配置反映**当时**的仓库结构、未随抽包更新；
> 当前结构以 [架构文档](../../architecture-harness.md) 为准。

# AI Harness 沙箱执行 + 外部 API（子项目③b-1）设计规格

- **日期**：2026-07-08
- **状态**：待实现（brainstorming 已定稿）
- **定位**：单用户自用工具，在①内核 + ②可靠性 + ③a 记忆之上扩展
- **前置**：①②③a 已完成并在 `main`

---

## 0. 背景与范围

子项目③b（沙箱执行）本规格覆盖：**③b-1 `Sandbox` 隔离原语 + 文件/shell/代码工具**，**外加外部 API/HTTP 工具**（原计划的 ③b-3，应要求并入）。**③b-2 浏览器工具**仍延后。

**两种不同的隔离**：
- **容器沙箱**（文件/shell/代码）：用**远程 Linux 云服务器的 Docker 容器**，经 **docker SDK over SSH**（`DOCKER_HOST=ssh://user@host`）驱动，每 session 惰性建临时容器、结束销毁。默认禁网。
- **外部 API/HTTP 工具**：跑在 **harness 进程内**（`httpx`），隔离来自**网络策略**——默认放行公网、拦截内网/元数据（SSRF 防护），可选域名白名单。

**存储/可观测性**：沿用 SQLite+sqlite-vec / OpenTelemetry（工具执行自动进②的 `tool_call` span）。

设计通则：严格 YAGNI；测试用 `LocalSandbox` + mock httpx，不碰云/Docker/真实网络；安全默认；不回归①②③a。

---

## 1. 范围与验收

### IN
- `Sandbox` 协议 + `DockerSandbox`（生产）+ `LocalSandbox`（测试/离线，**非安全边界**）。
- 容器工具（文件含读/写/列 3 个 + shell + 代码，共 5 个）：`write_file`/`read_file`/`list_files`、`run_shell`、`run_python`——在沙箱工作区内、路径受约束。
- **外部 API/HTTP 工具** `http_request`：进程内 `httpx`，**默认放行公网、拦截 loopback/私有网段/link-local/云元数据（SSRF 防护）**，配 `http_allowed_domains` 则切"仅白名单"；响应大小/超时/重定向次数上限、每跳重定向都过策略。

### OUT（后续/预留）
浏览器工具（③b-2）、容器内联网与 pip（默认禁网，留开关）、并发容器池、非 Python 语言执行。

### 验收标准
1. `LocalSandbox` 下 `run_python("print(1+1)")` 返回含 `2`；`write_file` 后 `read_file`/`list_files` 可见（工作区共享）。
2. 路径逃逸（`../../etc/passwd`、绝对路径、符号链接）被拒（`is_error`，不越界）。
3. `run_shell("sleep 999")` 在 `sandbox_exec_timeout` 后被终止、`timed_out=True`（不挂死）。
4. 输出超 `sandbox_output_max_chars` 截断。
5. 容器工具在 agent loop 被调用、结果回填、模型据此继续（mock 模型 + `LocalSandbox`）。
6. HTTP 工具：请求 `http://127.0.0.1/`、`http://169.254.169.254/`、私有网段主机被 SSRF 策略拒（`is_error`）；配了白名单时非白名单域名被拒；正常公网请求（mock）返回内容；响应超 `http_max_response_bytes` 截断/中止。
7. `DockerSandbox` 有 1 个可跳过集成测试（无 `sandbox_docker_host` 则 skip）。
8. ①②③a 原有测试不回归。

---

## 2. 架构与模块

**设计取向**：`Sandbox` 是协议（仿 `ModelClient`/`EmbeddingClient`），容器工具只依赖协议——生产注入 `DockerSandbox`、测试注入 `LocalSandbox`。HTTP 工具走独立的 `net/` 策略模块、进程内 `httpx`（不进容器）。工具执行自动走②的 `tool_call` span / 错误回填 / 截断。

```
src/harness/sandbox/           [新增]
├── __init__.py
├── base.py         Sandbox 协议 + ExecResult + SandboxError + resolve_in_workspace()
├── local.py        LocalSandbox（临时目录 + 子进程；测试/离线）
└── docker.py       DockerSandbox（docker SDK over SSH；远程临时容器 + 隔离策略）
src/harness/net/               [新增]
├── __init__.py
└── policy.py       check_url()（域名白名单 + SSRF/内网拦截）+ PolicyError
src/harness/tools/builtins/    [新增]
├── fs_tools.py     WriteFileTool / ReadFileTool / ListFilesTool
├── shell_tool.py   RunShellTool
├── code_tool.py    RunPythonTool
└── http_tool.py    HttpRequestTool（进程内 httpx + 策略 + 大小/超时/重定向上限）
src/harness/config.py          [改] docker + sandbox 资源上限 + http 网络策略等
```

**依赖新增**：`docker[ssh]`（Python SDK + paramiko）、`httpx`。`LocalSandbox` 仅标准库。

**边界**：
- `Sandbox` 协议——测试用 `LocalSandbox`；HTTP 工具用 mock httpx + 注入式 DNS 解析——都不碰真实网络/云。
- `DockerSandbox`/`LocalSandbox` 只认"隔离环境里执行"；`net/policy` 只认"URL 是否放行"；都不认 LLM/工具。
- 工具持有 `Sandbox`/策略配置，与①`CalculatorTool` 同构。
- **安全边界说明**：容器隔离来自 `DockerSandbox`（远程容器+策略），`LocalSandbox` 仅软约束、仅测试；HTTP 隔离来自 `net/policy`（SSRF 拦截 + 白名单）。

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

**路径约束**（`base.py::resolve_in_workspace(workspace, path)`）：把相对/绝对路径规约到工作区内，**任何逃出工作区的路径（`../`、绝对路径、符号链接逃逸）抛 `SandboxError`**。两实现共用。命令用 `list[str]`（不经 shell）避免注入；`run_shell` 是显式例外（`sh -c`）。

---

## 4. DockerSandbox（生产）

Python `docker` SDK，`base_url` 走 `ssh://user@host`（`sandbox_docker_host` 或 `DOCKER_HOST`）。

- **生命周期**：`start()` 惰性 `client.containers.run(image, command="sleep infinity", detach=True, **策略)`；文件/命令走 `container.exec_run`/`put_archive`/`get_archive`（tar 归档，无 shell 转义）；`close()` `container.remove(force=True)`。
- **隔离策略**（安全默认、可配置）：`network_mode="none"`、`user="1000:1000"`（非 root）、`read_only=True` + 工作区 `tmpfs`/卷可写、`mem_limit`、`nano_cpus`、`pids_limit`、`cap_drop=["ALL"]`、`security_opt=["no-new-privileges"]`、无 host bind 挂载。
- **超时**：`exec` 用 `timeout` 命令包裹或客户端计时；超时 → `timed_out=True`。
- **OTel**：容器创建 / 每次 exec 打 span（镜像、exit_code、耗时、timed_out）。

---

## 5. LocalSandbox（测试/离线）

- `start()` 建 `tempfile.mkdtemp()`；`close()` 删目录。
- `exec` 用 `asyncio.create_subprocess_exec`（**非 shell**）在工作区、带 `timeout`（超时 `kill()` + `timed_out=True`）；`run_shell` 走 `create_subprocess_shell`。
- 文件方法经 `resolve_in_workspace` 约束在临时目录内。
- **定位**：软约束、仅测试与离线开发，**非安全边界**（docstring 显著标注）。

---

## 6. 容器工具（文件 / shell / 代码）

都持有 `Sandbox` 引用，与①`CalculatorTool` 同构；结果含 stdout/stderr/exit_code、按 `sandbox_output_max_chars` 截断：

```python
class WriteFileTool(Tool):  name="write_file"  Params(path:str, content:str)
class ReadFileTool(Tool):   name="read_file"   Params(path:str)
class ListFilesTool(Tool):  name="list_files"  Params(path:str=".")
class RunShellTool(Tool):   name="run_shell"   Params(command:str)   # sandbox.exec(["sh","-c",cmd])
class RunPythonTool(Tool):  name="run_python"  Params(code:str)      # 写 workspace 临时 .py → exec(["python", f])
```

- `SandboxError`/超时/非零退出码兜成**可读 `is_error` 结果回填**（自动走②自纠正）。
- 路径逃逸尝试 → `is_error` + 明确提示。
- 装配：同一 `Sandbox` 实例注入这些工具；`Sandbox.start()` 惰性（首次调用时）。

---

## 6b. 外部 API/HTTP 工具（进程内，SSRF 防护）

**策略模块** `net/policy.py`：
```python
class PolicyError(Exception): ...
def check_url(url: str, allowed_domains: list[str], block_private: bool,
              resolve=default_resolve) -> None:
    # 1. 解析 host；allowed_domains 非空则 host 必须匹配其一（精确或子域），否则 PolicyError
    # 2. block_private：解析 host 的所有 IP，任一为 loopback/私有/link-local/reserved/multicast/未指定 → PolicyError
```
- SSRF 判定用 `ipaddress`：`is_private | is_loopback | is_link_local | is_reserved | is_multicast | is_unspecified`（`is_link_local` 覆盖 `169.254.169.254` 云元数据）。
- `resolve` 可注入（默认 `socket.getaddrinfo`），测试用假解析器，不碰真实 DNS。

**工具** `tools/builtins/http_tool.py::HttpRequestTool`：
```python
class HttpRequestTool(Tool):
    name = "http_request"
    description = "发起 HTTP 请求抓取网页或调用外部 API。"
    class Params(BaseModel):
        url: str
        method: str = "GET"
        headers: dict | None = None
        body: str | None = None
    def __init__(self, allowed_domains, block_private, timeout,
                 max_bytes, max_redirects, client_factory=..., resolve=...): ...
    async def run(self, p) -> str:
        # 手动跟随重定向、每一跳都 check_url；no-auto-redirect 的 httpx 请求；
        # 读取响应但上限 max_bytes（超则截断/中止）；返回状态码+头摘要+正文（截断）。
```
- **默认策略**：`http_allowed_domains=[]`（放行公网）+ `http_block_private=True`（拦截内网/元数据）。配了白名单即"仅白名单"。
- `PolicyError`/超时/超大响应 → **可读 `is_error` 回填**。
- 用 `httpx` 注入式 client（测试用 `httpx.MockTransport`）；`resolve` 注入（测试假 DNS）——不碰真实网络。

---

## 7. 配置 / 测试 / 依赖

### 新增配置（`config.py`，安全默认；默认 sandbox_backend=local 不碰云）
```
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

### 测试策略（`LocalSandbox` + mock httpx + 假 DNS，不碰云/网络）
- `resolve_in_workspace`：`../`、绝对路径、符号链接逃逸抛 `SandboxError`。
- `LocalSandbox`：`start/close`；`exec` echo；`write/read/list` 往返共享工作区；超时被 kill、`timed_out=True`。
- 容器工具经 `ToolExecutor`：`write_file`→`read_file` 往返；`run_python("print(1+1)")`→含 `2`；逃逸路径 → `is_error`；输出截断。
- `net/policy.check_url`：`127.0.0.1`/`169.254.169.254`/`10.x` 假解析被拒；白名单命中/未命中；公网放行。
- `HttpRequestTool`：`httpx.MockTransport` 正常响应返回内容；SSRF/白名单拒 → `is_error`；超大响应截断；重定向到内网被每跳策略拦截。
- 集成：`AgentLoop` + mock 模型（发 `run_python`/`http_request`）+ `LocalSandbox`/mock httpx → 断言回填。
- `DockerSandbox`：1 个 `@pytest.mark.skipif(not sandbox_docker_host)` 集成测试。

### 依赖新增
`docker[ssh]`（Python SDK + paramiko）、`httpx`。`LocalSandbox` 仅标准库。

---

## 8. 后续衔接（备忘，非本次范围）

- **③b-2 浏览器工具**：Playwright headless 抓网页（动态渲染场景，补 `http_request` 抓不动的）。
- **③c 多 Agent 编排**、**③d 持久化**。
- 容器联网开关（放开 `sandbox_network`）+ pip 安装：需要时再放开，与 HTTP 策略配合。
- App 层：代码执行/文件/HTTP 工具支撑"执行代码或脚本""抓网上数据""调用外部 API"等学习助手功能。

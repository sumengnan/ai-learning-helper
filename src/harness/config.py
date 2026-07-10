from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class HarnessConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HARNESS_",
        env_file=".env",
        extra="ignore",
        protected_namespaces=(),
    )

    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    system_prompt: str = "You are a helpful assistant."
    max_steps: int = 10
    temperature: float = 0.7
    request_timeout: float = 60.0
    max_retries: int = 2
    retry_base_delay: float = 0.5
    max_tokens_budget: int | None = None
    max_wall_seconds: float | None = None
    tool_result_max_chars: int = 8000
    include_usage: bool = True
    otel_enabled: bool = False
    otel_exporter: str = "console"      # console | otlp
    otel_endpoint: str = ""
    price_map: dict = {}                 # {model: [in_per_1k, out_per_1k]}
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str = ""          # 空则回退用 api_key
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 1536
    memory_db_path: str = "memory.db"
    chunk_size: int = 1000
    chunk_overlap: int = 200
    search_top_k: int = 5
    memory_collection: str = "knowledge"
    # 容器沙箱
    sandbox_backend: str = "local"          # local | docker
    sandbox_docker_host: str = ""           # tcp://host:2376（Docker daemon 的 TLS 端口）
    # 直连 Docker daemon TLS 端口的双向 TLS 证书（不再走 SSH）
    sandbox_docker_tls_ca_cert: str = ""        # CA 证书路径（校验服务端）
    sandbox_docker_tls_client_cert: str = ""    # 客户端证书路径
    sandbox_docker_tls_client_key: str = ""     # 客户端私钥路径
    sandbox_docker_tls_verify: bool = True      # 是否校验服务端证书
    sandbox_image: str = "python:3.12-slim"     # 路由未启用时的单镜像；也是 base/shell 容器镜像
    # 语言->镜像；空=禁用路由（向后兼容单容器）。
    # 例: {"python":"python:3.12-slim","node":"node:20-slim","java":"eclipse-temurin:21-jdk"}
    sandbox_images: dict = {}
    sandbox_default_language: str = "python"     # 协议方法（shell/fs）委托到的容器语言
    sandbox_approval_timeout: float = 120.0      # 危险命令人工确认超时（秒）；超时自动拒绝
    sandbox_workspace: str = "/workspace"
    sandbox_user: str = "1000:1000"
    sandbox_network: str = "none"
    sandbox_mem_limit: str = "512m"
    sandbox_cpus: float = 1.0
    sandbox_pids_limit: int = 128
    sandbox_read_only: bool = False         # 容器根文件系统是否只读（默认可写）
    sandbox_exec_timeout: float = 30.0
    sandbox_output_max_chars: int = 8000
    # 会话级沙箱空闲驱逐（秒）：某会话超过此时长无沙箱操作则销毁其容器（安全阀，防泄漏）。
    # 与「删除会话即销毁」的主路径无关；<=0 关闭空闲驱逐。默认 30 分钟。
    sandbox_idle_timeout: float = 1800.0
    # 外部 API/HTTP
    http_allowed_domains: list = []         # 空=放行公网；非空=仅白名单
    http_block_private: bool = True         # SSRF：拦截内网/元数据
    http_timeout: float = 30.0
    http_max_response_bytes: int = 5_000_000
    http_max_redirects: int = 5
    # 浏览器
    browser_headless: bool = True
    browser_nav_timeout: float = 30.0
    browser_wait_until: str = "networkidle"   # load | domcontentloaded | networkidle
    browser_output_max_chars: int = 8000
    browser_user_agent: str = ""
    # 在沙箱容器内跑无头 Chromium 时的启动参数（cap_drop=ALL/非 root/小 shm 下必备）
    sandbox_browser_launch_args: list = [
        "--no-sandbox", "--disable-setuid-sandbox",
        "--disable-dev-shm-usage", "--disable-gpu",
    ]
    # 技能（渐进式披露）
    skills_dir: str = "skills"            # 技能目录：<skills_dir>/<name>/SKILL.md
    skill_resource_max_chars: int = 8000  # read_skill_resource 单次读取上限
    # 多 Agent 编排
    agents_dir: str = "agents"        # 子 agent 花名册目录：<agents_dir>/<name>.yaml
    max_dispatch_depth: int = 2       # agent 树最大层数（防无限递归）
    sub_agent_max_steps: int = 10     # 子 agent 单次 run 步数上限
    # 情景记忆
    episode_collection: str = "episodes"
    episode_recall_k: int = 3
    # 持久化
    persistence_db_path: str = "harness.db"

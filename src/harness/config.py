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
    # 浏览器
    browser_headless: bool = True
    browser_nav_timeout: float = 30.0
    browser_wait_until: str = "networkidle"   # load | domcontentloaded | networkidle
    browser_output_max_chars: int = 8000
    browser_user_agent: str = ""
    # 多 Agent 编排
    max_dispatch_depth: int = 2       # agent 树最大层数（防无限递归）
    sub_agent_max_steps: int = 10     # 子 agent 单次 run 步数上限

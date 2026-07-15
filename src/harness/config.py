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
    # 透传给 chat.completions.create 的额外请求体（默认空=不改变行为）。用于开关厂商私有参数，
    # 例如 Qwen3 关闭「思考模式」提速：HARNESS_LLM_EXTRA_BODY={"enable_thinking": false}
    # （DashScope 百炼 compatible-mode 用此形式；自托管 vLLM 用
    # {"chat_template_kwargs": {"enable_thinking": false}}）。
    llm_extra_body: dict = {}
    max_retries: int = 2
    retry_base_delay: float = 0.5
    max_tokens_budget: int | None = None
    max_wall_seconds: float | None = None
    tool_result_max_chars: int = 8000
    include_usage: bool = True
    otel_enabled: bool = False
    otel_exporter: str = "console"      # console | otlp
    otel_endpoint: str = ""
    price_map: dict = {}                 # {model: [in_per_1k, out_per_1k]}（旧版扁平计费，实时口径）
    # 当前模型分层计费：按「输入长度」分档，每档 [输入上限tokens, 输入价/百万token, 输出价/百万token]，
    # 升序排列，末档为封顶价；空=未知（成本显示 —）。默认 qwen-plus 档位（¥/百万 token）：
    # 输入≤256K=1.6、256K~1M=4.8；输出≤256K=6.4、256K~1M=19.2。
    model_price_tiers: list = [[256000, 1.6, 6.4], [1000000, 4.8, 19.2]]
    price_currency: str = "¥"            # 估算成本显示的货币符号
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str = ""          # 空则回退用 api_key
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 1536
    memory_db_path: str = "memory.db"
    chunk_size: int = 1000
    chunk_overlap: int = 200
    chunk_hard_max: int = 2000        # 切分容量硬上限：表格/代码块在此上限内整块保留，绝不超此值
    search_top_k: int = 5
    memory_collection: str = "knowledge"
    retrieval_candidate_pool: int = 20
    retrieval_w_relevance: float = 1.0
    retrieval_w_recency: float = 0.2
    retrieval_w_importance: float = 0.1
    retrieval_recency_half_life_days: float = 30.0
    retrieval_use_keyword: bool = True
    retrieval_use_mmr: bool = True
    retrieval_mmr_lambda: float = 0.7
    retrieval_rrf_k: int = 60
    # 查询期召回增强（默认全关=零行为变更）；开启需 embedding 已配（有 memory）。
    # 三路各加一路召回并入 RRF，共享一次查询期 LLM 规划（QueryPlanner），带超时降级。
    retrieval_use_entity_recall: bool = False   # LLM 从 query 抽实体键 → list_by_entity 精确取
    retrieval_use_multi_query: bool = False      # LLM 改写多条等价查询各跑向量召回
    retrieval_use_hyde: bool = False             # LLM 生成假设答案文档 → 其向量召回
    retrieval_multi_query_n: int = 3
    retrieval_query_plan_timeout_s: float = 2.0  # 规划 LLM 超时（首字关键路径，超时即降级）
    # 精排（rerank）：默认关=维持现状（NoOpReranker）。开启且配了端点+模型才生效，
    # 全局作用于所有检索路径（知识库/题库/对话记忆/search_memory）。端点须为
    # OpenAI/Cohere/Jina 兼容的 POST {base}/rerank。失败自动降级为原序，不打断检索。
    enable_rerank: bool = False
    rerank_style: str = "openai"         # openai（Cohere/Jina/SiliconFlow 兼容）| dashscope（千问 qwen）
    rerank_base_url: str = ""             # openai 风格填到 /v1；dashscope 填完整 endpoint
    rerank_api_key: str = ""             # 空则回退 embedding_api_key → api_key
    rerank_model: str = ""               # 如 BAAI/bge-reranker-v2-m3 或 qwen3-rerank
    rerank_timeout: float = 30.0
    rerank_top_n: int = 0                # 0=送全部候选精排；>0 只精排前 N（省调用成本）
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
    sandbox_images: dict = {"python":"python:3.12","node":"node:20","java":"eclipse-temurin:21-jdk","go":"golang:1.22","rust":"rust:1.77","ruby":"ruby:3.3","php":"php:8.3","perl":"perl:5.38","dotnet":"mcr.microsoft.com/dotnet/sdk:8.0","cpp":"gcc:13","c":"gcc:13","clang":"silkeh/clang:17","swift":"swift:5.10","kotlin":"eclipse-temurin:21-jdk","scala":"sbtscala/scala-sbt:eclipse-temurin-21.0.2_13_1.9.9_3.4.2","clojure":"clojure:temurin-21-tools-deps","groovy":"groovy:4.0-jdk21","dart":"dart:3.4","elixir":"elixir:1.16","erlang":"erlang:26","haskell":"haskell:9.8","julia":"julia:1.10","r":"r-base:4.4.0","lua":"nickblah/lua:5.4","nim":"nimlang/nim:2.0.4","crystal":"crystallang/crystal:1.12.1","typescript":"node:20","deno":"denoland/deno:1.43.6","bun":"oven/bun:1.1","ocaml":"ocaml/opam:debian-12-ocaml-5.1","fsharp":"mcr.microsoft.com/dotnet/sdk:8.0","vlang":"thevlang/vlang:latest","zig":"ziglang/static-base:0.12.0","fortran":"gcc:13","cobol":"esolang/cobol:latest","bash":"bash:5.2","powershell":"mcr.microsoft.com/powershell:7.4-ubuntu-22.04"}
    sandbox_default_language: str = "python"     # 协议方法（shell/fs）委托到的容器语言
    # 语言[+版本]->镜像；配置后 run_python/run_node/run_java 会按语言[+可选 version]
    # 另起一次性子沙箱执行（跑完即销毁、产物回传会话基础容器）。key 优先 f"{language}{version}"
    # （回退 language）；显式指定的 version 无对应镜像则报错。空=不启用子沙箱（代码在会话
    # 基础容器内直接执行，向后兼容）。
    # 例: {"python":"python:3.12-slim","node":"node:20-slim","java":"eclipse-temurin:21-jdk",
    #      "java8":"eclipse-temurin:8-jdk","java11":"eclipse-temurin:11-jdk",
    #      "java17":"eclipse-temurin:17-jdk","java21":"eclipse-temurin:21-jdk"}
    sandbox_lang_images: dict = {"python":"python:3.12-slim","node":"node:20-slim","java":"eclipse-temurin:21-jdk",
          "java8":"eclipse-temurin:8-jdk","java11":"eclipse-temurin:11-jdk",
          "java17":"eclipse-temurin:17-jdk","java21":"eclipse-temurin:21-jdk"}
    sandbox_sub_network: str = "none"            # 一次性代码子沙箱的网络（默认禁网；需 pip/maven 取包时置 bridge）
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
    # 空 UA 是最典型的爬虫特征之一，不少站点据此直接 403——此前本工具一个 UA 都不发。
    # 默认按「行为良好的爬虫」惯例如实标明身份（Googlebot 也是这个格式），能解决「仅因为
    # 没有 UA 而被拒」的那批站点。设为空字符串则退回不发 UA。
    # 注：这不会绕过 Cloudflare 之类的 bot 检测——那条路本就走浏览器兜底。若确需伪装成
    # 浏览器，自行用 HARNESS_HTTP_USER_AGENT 覆盖（是否合规由使用者自行判断）。
    http_user_agent: str = "Mozilla/5.0 (compatible; AI-Learning-Helper/1.0; +harness)"
    # 浏览器
    browser_headless: bool = True
    browser_nav_timeout: float = 30.0
    browser_wait_until: str = "networkidle"   # load | domcontentloaded | networkidle
    browser_output_max_chars: int = 8000
    browser_user_agent: str = ""
    # 浏览器专用沙箱镜像（须含 Playwright+Chromium+curl，如 mcr playwright 镜像 + curl）。
    # 配了则每次抓取在该镜像的一次性子沙箱内跑 Chromium，基础镜像可保持轻量（如 python:3.12
    # 无需装 playwright）；留空则复用基础容器（需基础镜像自带 playwright，否则 browse 会报
    # ModuleNotFoundError: No module named 'playwright'）。
    browser_sandbox_image: str = ""
    # 浏览器子沙箱的内存上限：Chromium 远比一般沙箱吃内存，若沿用基础沙箱的小额度
    # （如 100m）会被 OOM 杀掉，容器中途消失、browse 失败。故单列一档，默认 1g。
    browser_sandbox_mem_limit: str = "1g"
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
    # MCP（Model Context Protocol）客户端：连接外部/内置 MCP server，把远程工具暴露为本地工具。
    # server 清单在下面这个 JSON 文件里声明（stdio + streamable-http 双传输）；改配置后重启
    # （或调 POST /api/mcp/reload）生效。总开关 enable_mcp 在 AppConfig。
    mcp_config_path: str = "mcp/mcp_servers.json"   # server 清单文件路径
    mcp_connect_timeout: float = 15.0           # 单 server 连接/初始化超时（秒），超时跳过

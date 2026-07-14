# app/config.py
from __future__ import annotations

from pydantic_settings import SettingsConfigDict

from harness.config import HarnessConfig


class AppConfig(HarnessConfig):
    model_config = SettingsConfigDict(
        env_prefix="HARNESS_", env_file=".env", extra="ignore", protected_namespaces=())

    app_host: str = "127.0.0.1"
    app_port: int = 8000
    auth_secret: str = "dev-insecure-secret-change-me"
    # 登录/注册是否强制图形验证码（后端校验）。默认关，便于测试直连；
    # 生产用 HARNESS_REQUIRE_CAPTCHA=true 打开。前端始终展示并回传验证码。
    require_captcha: bool = False
    # 应用领域各表统一存于此单一数据库文件（可用 HARNESS_APP_DB_PATH 覆盖）
    app_db_path: str = "app.db"
    app_system_prompt: str = "你是一个 AI 学习助手，可用工具检索知识、联网、计算来帮助用户学习。"
    enable_browser: bool = False
    enable_sandbox: bool = False
    enable_dispatch: bool = False
    enable_skills: bool = False
    enable_mcp: bool = False          # MCP 客户端总开关；开则按 mcp_config_path 连接 server
    cors_origins: list = ["http://localhost:5173"]
    app_max_upload_mb: int = 20
    quiz_max_count: int = 20
    quiz_retrieve_k: int = 6
    short_pass_score: int = 60
    downloads_dir: str = "downloads"
    download_max_mb: int = 25
    # 回答交付前校验门（默认关，保持现状直通流式）
    enable_answer_gate: bool = False
    answer_gate_max_retries: int = 1        # 校验不过时的自动重答次数（N）；总尝试 = N+1
    answer_pass_score: int = 70             # LLM 自评打分阈值（低于则不过）
    gate_check_format: bool = True          # 分项开关：格式/完整性
    gate_check_grounding: bool = True       # 分项开关：知识库 grounding
    gate_check_code: bool = True            # 分项开关：代码可运行
    gate_check_judge: bool = True           # 分项开关：LLM 自评打分
    gate_check_facts: bool = False          # 分项开关：引用链接可达性核对
    gate_check_consistency: bool = True     # 分项开关：声称完成动作但零工具调用 → 判不一致、重做
    # 每步校验（实时层，规则/阈值为主，内核零改动）
    enable_step_check: bool = True          # 高风险步实时校验（检索相关性/代码执行）
    step_relevance_min: float = 0.0         # 检索低分阈值；0=只判空命中（起步）
    # 轨迹 judge（评估层，交付前一次性回看整轨迹分层打分）
    enable_trajectory_judge: bool = False   # 与 answer gate 独立，可单独开
    trajectory_pass_score: int = 60         # 最终层分数阈值（低于则软门不过）
    judge_model: str = ""                   # 独立 judge 模型；空则回退主 model
    judge_base_url: str = ""                # judge 独立端点；空则回退主 base_url
    judge_api_key: str = ""                 # judge 独立 key；空则回退主 api_key
    judge_samples: int = 1                  # 预留：多次取多数（起步 1）
    # 聊天附件：裸字节落盘目录、单文件上限、单会话待发数量上限、可直接喂视觉模型的图片上限
    attachments_dir: str = "attachments"
    attachment_max_mb: int = 100
    attachment_max_count: int = 10
    attachment_vision_max_mb: int = 5
    memory_write_extract: bool = False
    memory_write_sample_rate: float = 1.0
    memory_write_candidate_k: int = 5
    ttl_episodic_days: int = 0
    ttl_semantic_days: int = 0
    ttl_procedural_days: int = 0
    consolidation_sim_threshold: float = 0.85
    consolidation_min_cluster: int = 2
    consolidation_max_source: int = 200
    # 分层上下文管理：full=全量拼接（默认，与历史行为字节级一致，安全回退）；
    # window=仅 L1 token 预算滑动窗口；layered=L1+L2 滚动摘要+L3 语义检索。
    context_strategy: str = "full"                 # full | window | layered
    context_window_tokens: int = 128000            # 模型上下文窗口（按实际模型调整）
    context_response_reserve_tokens: int = 4096    # 给回复预留的 token
    context_working_ratio: float = 0.5             # 最近原文（L1）占可用预算的比例
    context_summary_max_tokens: int = 2000         # L2 摘要块 token 上限
    context_retrieval_top_k: int = 5               # L3 召回条数
    context_enable_summary: bool = True            # layered 下是否启用 L2 摘要
    context_enable_retrieval: bool = True          # layered 下是否启用 L3 检索

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
    # 聊天附件：裸字节落盘目录、单文件上限、单会话待发数量上限、可直接喂视觉模型的图片上限
    attachments_dir: str = "attachments"
    attachment_max_mb: int = 100
    attachment_max_count: int = 10
    attachment_vision_max_mb: int = 5

# app/config.py
from __future__ import annotations

from pydantic_settings import SettingsConfigDict

from harness.config import HarnessConfig


class AppConfig(HarnessConfig):
    model_config = SettingsConfigDict(
        env_prefix="HARNESS_", env_file=".env", extra="ignore", protected_namespaces=())

    app_host: str = "127.0.0.1"
    app_port: int = 8000
    conversations_db_path: str = "conversations.db"
    app_system_prompt: str = "你是一个 AI 学习助手，可用工具检索知识、联网、计算来帮助用户学习。"
    enable_browser: bool = False
    enable_sandbox: bool = False
    enable_dispatch: bool = False
    cors_origins: list = ["http://localhost:5173"]
    app_max_upload_mb: int = 20
    documents_db_path: str = "documents.db"
    questions_db_path: str = "questions.db"
    exams_db_path: str = "exams.db"
    wrong_answers_db_path: str = "wrong_answers.db"
    quiz_max_count: int = 20
    quiz_retrieve_k: int = 6
    short_pass_score: int = 60
    downloads_dir: str = "downloads"
    downloads_db_path: str = "downloads.db"
    download_max_mb: int = 25

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

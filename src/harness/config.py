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

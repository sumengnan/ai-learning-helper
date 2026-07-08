from harness.config import HarnessConfig


def test_defaults():
    cfg = HarnessConfig(api_key="k")
    assert cfg.model == "gpt-4o-mini"
    assert cfg.max_steps == 10
    assert cfg.base_url.endswith("/v1")


def test_env_override(monkeypatch):
    monkeypatch.setenv("HARNESS_MODEL", "deepseek-chat")
    monkeypatch.setenv("HARNESS_MAX_STEPS", "3")
    cfg = HarnessConfig(api_key="k")
    assert cfg.model == "deepseek-chat"
    assert cfg.max_steps == 3


def test_reliability_defaults():
    cfg = HarnessConfig(api_key="k")
    assert cfg.max_retries == 2
    assert cfg.retry_base_delay == 0.5
    assert cfg.max_tokens_budget is None
    assert cfg.max_wall_seconds is None
    assert cfg.tool_result_max_chars == 8000
    assert cfg.otel_enabled is False
    assert cfg.otel_exporter == "console"
    assert cfg.price_map == {}


def test_memory_defaults():
    cfg = HarnessConfig(api_key="k")
    assert cfg.embedding_base_url.endswith("/v1")
    assert cfg.embedding_model == "text-embedding-3-small"
    assert cfg.embedding_dimension == 1536
    assert cfg.memory_db_path == "memory.db"
    assert cfg.chunk_size == 1000
    assert cfg.chunk_overlap == 200
    assert cfg.search_top_k == 5
    assert cfg.memory_collection == "knowledge"

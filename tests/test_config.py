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

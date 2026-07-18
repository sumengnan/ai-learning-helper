from app.config import AppConfig


def test_orchestrator_config_defaults(monkeypatch):
    monkeypatch.setenv("HARNESS_API_KEY", "")
    cfg = AppConfig()
    assert cfg.enable_orchestrator is False
    assert cfg.orchestrator_max_step_retry == 2
    assert cfg.orchestrator_max_replan == 2
    assert cfg.orchestrator_planner_max_retries == 2
    assert cfg.orchestrator_step_max_steps >= 1


def test_orchestrator_config_env_override(monkeypatch):
    monkeypatch.setenv("HARNESS_ENABLE_ORCHESTRATOR", "true")
    monkeypatch.setenv("HARNESS_ORCHESTRATOR_MAX_REPLAN", "3")
    cfg = AppConfig()
    assert cfg.enable_orchestrator is True
    assert cfg.orchestrator_max_replan == 3

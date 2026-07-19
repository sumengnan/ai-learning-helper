from app.config import AppConfig


def test_orchestrator_config_defaults(monkeypatch):
    monkeypatch.setenv("HARNESS_API_KEY", "")
    cfg = AppConfig()
    # enable_orchestrator 开关已移除：编排器是唯一主流程，无需开关
    assert not hasattr(cfg, "enable_orchestrator")
    assert cfg.orchestrator_max_step_retry == 2
    assert cfg.orchestrator_max_replan == 2
    assert cfg.orchestrator_planner_max_retries == 2
    assert cfg.orchestrator_step_max_steps >= 1


def test_orchestrator_config_env_override(monkeypatch):
    monkeypatch.setenv("HARNESS_ORCHESTRATOR_MAX_REPLAN", "3")
    cfg = AppConfig()
    assert cfg.orchestrator_max_replan == 3

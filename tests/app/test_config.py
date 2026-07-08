from app.config import AppConfig


def test_app_defaults():
    cfg = AppConfig(api_key="k")
    assert cfg.app_host == "127.0.0.1"
    assert cfg.app_port == 8000
    assert cfg.conversations_db_path == "conversations.db"
    assert cfg.enable_sandbox is False
    assert cfg.model == "gpt-4o-mini"      # 继承 HarnessConfig

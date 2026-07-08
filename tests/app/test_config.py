from app.config import AppConfig


def test_app_defaults():
    cfg = AppConfig(api_key="k")
    assert cfg.app_host == "127.0.0.1"
    assert cfg.app_port == 8000
    assert cfg.conversations_db_path == "conversations.db"
    assert cfg.enable_sandbox is False
    assert cfg.model == "gpt-4o-mini"      # 继承 HarnessConfig


def test_app2_config_defaults():
    cfg = AppConfig(api_key="k")
    assert cfg.app_max_upload_mb == 20
    assert cfg.documents_db_path == "documents.db"

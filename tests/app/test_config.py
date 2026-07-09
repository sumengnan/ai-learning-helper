from app.config import AppConfig


def test_app_defaults():
    # _env_file=None：断言源码默认值，不受开发机本地 .env 影响
    cfg = AppConfig(api_key="k", _env_file=None)
    assert cfg.app_host == "127.0.0.1"
    assert cfg.app_port == 8000
    assert cfg.app_db_path == "app.db"
    assert cfg.enable_sandbox is False
    assert cfg.model == "gpt-4o-mini"      # 继承 HarnessConfig


def test_app2_config_defaults():
    cfg = AppConfig(api_key="k")
    assert cfg.app_max_upload_mb == 20
    assert cfg.downloads_dir == "downloads"

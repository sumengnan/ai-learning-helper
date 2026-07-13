# app/__main__.py
import uvicorn

from .config import AppConfig
from .logging_setup import configure_logging

if __name__ == "__main__":
    configure_logging()
    cfg = AppConfig()
    uvicorn.run("app.main:create_app", host=cfg.app_host, port=cfg.app_port, factory=True)

# app/__main__.py
import uvicorn

from .config import AppConfig

if __name__ == "__main__":
    cfg = AppConfig()
    uvicorn.run("app.main:create_app", host=cfg.app_host, port=cfg.app_port, factory=True)

# tests/app/test_version_api.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.version import make_version_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(make_version_router())
    return TestClient(app)


def test_version_reports_injected_build_info(monkeypatch):
    monkeypatch.setenv("APP_GIT_SHA", "abc1234")
    monkeypatch.setenv("APP_BUILD_TIME", "2026-07-12T00:00:00Z")
    body = _client().get("/api/version").json()
    assert body["git_sha"] == "abc1234"
    assert body["built_at"] == "2026-07-12T00:00:00Z"
    assert body["version"]  # 非空版本号


def test_version_falls_back_to_dev_when_not_injected(monkeypatch):
    monkeypatch.delenv("APP_GIT_SHA", raising=False)
    monkeypatch.delenv("APP_BUILD_TIME", raising=False)
    body = _client().get("/api/version").json()
    assert body["git_sha"] == "dev"
    assert body["built_at"] == ""


def test_version_is_public_no_auth_required():
    # 部署自检要在登录前也能看到版本，故该端点不挂鉴权
    assert _client().get("/api/version").status_code == 200

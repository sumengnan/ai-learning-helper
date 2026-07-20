# tests/app/test_models_api.py
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.models_info import make_models_router
from app.auth import current_user


def _client(config) -> TestClient:
    app = FastAPI()
    app.include_router(make_models_router(config))
    app.dependency_overrides[current_user] = lambda: "u1"   # 绕过鉴权
    return TestClient(app)


def test_models_reports_resolved_names():
    cfg = SimpleNamespace(model="deepseek-v4-flash", fast_model="qwen-turbo",
                          judge_model="qwen3.5-flash",
                          embedding_model="text-embedding-v4", rerank_model="")
    body = _client(cfg).get("/api/models").json()
    assert body == {"main": "deepseek-v4-flash", "fast": "qwen-turbo",
                    "judge": "qwen3.5-flash", "embedding": "text-embedding-v4", "rerank": None}


def test_models_fast_judge_fallback_to_main():
    cfg = SimpleNamespace(model="m", fast_model="", judge_model="",
                          embedding_model="", rerank_model="")
    body = _client(cfg).get("/api/models").json()
    assert body["fast"] == "m" and body["judge"] == "m"          # 未配 → 回退主模型
    assert body["embedding"] is None and body["rerank"] is None  # 未配 → None

# app/api/version.py
from __future__ import annotations

import os

from fastapi import APIRouter

# version / git_sha / built_at 均由镜像构建时经 build-arg 注入为环境变量
# （见 Dockerfile 的 APP_VERSION / APP_GIT_SHA / APP_BUILD_TIME）：
# - version 是 CI 自增的语义版本号（0.0.1 起，也是 Docker Hub 镜像 tag）
# - git_sha / built_at 供追溯
# 本地未注入时回退 "dev"，便于一眼区分"是否走了 CI 构建"。


def make_version_router() -> APIRouter:
    router = APIRouter()

    # 不挂鉴权：部署自检需在登录前也能读到，且不含敏感信息。
    @router.get("/api/version")
    async def version() -> dict:
        return {
            "version": os.environ.get("APP_VERSION", "dev"),
            "git_sha": os.environ.get("APP_GIT_SHA", "dev"),
            "built_at": os.environ.get("APP_BUILD_TIME", ""),
        }

    return router

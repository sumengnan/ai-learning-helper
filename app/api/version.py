# app/api/version.py
from __future__ import annotations

import os

from fastapi import APIRouter

# 后端版本号（与 pyproject 保持一致的手填值即可）。git_sha / built_at 由镜像构建时
# 经 build-arg 注入为环境变量（见 Dockerfile 的 APP_GIT_SHA / APP_BUILD_TIME）；
# 本地未注入时回退 "dev"，便于一眼区分"是否走了 CI 构建"。
_VERSION = "0.1.0"


def make_version_router() -> APIRouter:
    router = APIRouter()

    # 不挂鉴权：部署自检需在登录前也能读到，且不含敏感信息。
    @router.get("/api/version")
    async def version() -> dict:
        return {
            "version": _VERSION,
            "git_sha": os.environ.get("APP_GIT_SHA", "dev"),
            "built_at": os.environ.get("APP_BUILD_TIME", ""),
        }

    return router

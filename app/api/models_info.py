# app/api/models_info.py
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import current_user


def make_models_router(config) -> APIRouter:
    """暴露各角色当前使用的模型名，供前端在聊天区展示「当前模型」。

    快速/judge 未单独配 model 时回退主模型（与 completion._alt_config 的回退语义一致）；
    embedding/rerank 未配则为空。仅返回模型名，不含端点/密钥等敏感信息。
    """
    router = APIRouter()

    @router.get("/api/models")
    async def models(_: str = Depends(current_user)) -> dict:
        return {
            "main": config.model,
            "fast": config.fast_model or config.model,
            "judge": config.judge_model or config.model,
            "embedding": config.embedding_model or None,
            "rerank": config.rerank_model or None,
        }

    return router

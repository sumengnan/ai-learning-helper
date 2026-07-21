# app/api/documents.py
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..auth import current_user
from ..knowledge import EmptyDocument

log = logging.getLogger("app.documents")
from ..parsing import ParseError, UnsupportedFormat


def make_documents_router(service, doc_store, config) -> APIRouter:
    router = APIRouter()

    @router.post("/api/documents")
    async def upload(file: UploadFile = File(...), user_id: str = Depends(current_user)):
        if service is None:
            raise HTTPException(status_code=503, detail="知识库未启用（未配置 embedding）")
        limit = config.app_max_upload_mb * 1024 * 1024
        if file.size is not None and file.size > limit:  # 读入内存前先按声明大小拦截
            raise HTTPException(status_code=413, detail=f"文件超过 {config.app_max_upload_mb}MB")
        data = await file.read()
        if len(data) > limit:  # 兜底：size 缺失或不实时按实际字节再判
            raise HTTPException(status_code=413, detail=f"文件超过 {config.app_max_upload_mb}MB")
        try:
            return await service.ingest(user_id, file.filename, data)
        except UnsupportedFormat as e:
            raise HTTPException(status_code=400, detail=f"不支持的格式：{e}")
        except ParseError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except EmptyDocument:
            raise HTTPException(status_code=400, detail="文档为空或无法提取文本")
        except Exception as e:   # noqa: BLE001
            # 兜底：入库要调 embedding 端点，那里的失败（限流、超时、密钥无效、批量超限…）
            # 此前没人接，裸 openai.BadRequestError 直接冒成 500，用户只看到「上传失败」，
            # 连是自己的问题还是服务的问题都判断不了。原文进日志供排查，界面给人话。
            log.warning("知识库入库失败 file=%s：%s", file.filename, e, exc_info=True)
            raise HTTPException(status_code=502, detail=_ingest_hint(e))

    @router.get("/api/documents")
    async def list_fragments(page: int = 1, size: int = 8, category: str = "",
                             user_id: str = Depends(current_user)):
        # 知识库以切分后的片段（chunk）为展示单元，每片一项；category 非空则按分类筛选
        if service is None:
            return {"items": [], "total": 0}
        size = max(1, min(100, size))
        return service.list_fragments(user_id, max(1, page), size, category or None)

    @router.get("/api/documents/search")
    async def search_fragments(q: str = "", k: int = 30,
                               user_id: str = Depends(current_user)):
        if service is None:
            raise HTTPException(status_code=503, detail="知识库未启用（未配置 embedding）")
        if not q.strip():
            return []
        return await service.search(user_id, q.strip(), k)

    @router.get("/api/documents/{chunk_id}")
    async def get_fragment(chunk_id: str, user_id: str = Depends(current_user)):
        if service is None:
            raise HTTPException(status_code=503, detail="知识库未启用")
        frag = service.get_fragment(user_id, chunk_id)
        if frag is None:
            raise HTTPException(status_code=404, detail="片段不存在")
        return frag

    @router.delete("/api/documents/{chunk_id}")
    async def delete_fragment(chunk_id: str, user_id: str = Depends(current_user)):
        if service is None:
            raise HTTPException(status_code=503, detail="知识库未启用")
        if not service.delete_fragment(user_id, chunk_id):
            raise HTTPException(status_code=404, detail="片段不存在")
        return {"ok": True}

    return router


# 向量化端点的失败原文对用户毫无意义（一长串 openai.BadRequestError + 厂商错误码），
# 但错误里往往藏着「该找谁修」的关键信息。按可辨识的特征给出对应的下一步动作，
# 认不出的才回退到通用文案 —— 通用文案是兜底，不是默认。
_INGEST_HINTS = (
    (("batch size", "too many", "max_batch"),
     "文档切分后的片段数超过了向量化服务单次上限。请把文档拆成几个小文件分别上传。"),
    (("rate limit", "429", "too many requests", "throttl"),
     "向量化服务限流了，请稍等一会儿再上传。"),
    (("timeout", "timed out", "connection", "connect"),
     "连不上向量化服务，请检查网络或稍后重试。"),
    (("api key", "unauthorized", "401", "invalid_api_key", "authentication"),
     "向量化服务的密钥无效或已过期，请联系管理员检查 HARNESS_EMBEDDING_API_KEY。"),
    (("quota", "insufficient", "balance", "arrearage"),
     "向量化服务额度不足，请联系管理员充值后重试。"),
    (("dimension",),
     "向量维度与知识库不一致，请联系管理员核对 HARNESS_EMBEDDING_DIMENSION。"),
)


def _ingest_hint(exc: Exception) -> str:
    low = str(exc).lower()
    for keys, msg in _INGEST_HINTS:
        if any(k in low for k in keys):
            return f"上传失败：{msg}"
    return "上传失败：向量化服务暂时不可用，请稍后重试；若持续失败请联系管理员查看服务端日志。"

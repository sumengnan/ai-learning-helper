# app/api/documents.py
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..knowledge import EmptyDocument
from ..parsing import ParseError, UnsupportedFormat


def make_documents_router(service, doc_store, config) -> APIRouter:
    router = APIRouter()

    @router.post("/api/documents")
    async def upload(file: UploadFile = File(...)):
        if service is None:
            raise HTTPException(status_code=503, detail="知识库未启用（未配置 embedding）")
        limit = config.app_max_upload_mb * 1024 * 1024
        if file.size is not None and file.size > limit:  # 读入内存前先按声明大小拦截
            raise HTTPException(status_code=413, detail=f"文件超过 {config.app_max_upload_mb}MB")
        data = await file.read()
        if len(data) > limit:  # 兜底：size 缺失或不实时按实际字节再判
            raise HTTPException(status_code=413, detail=f"文件超过 {config.app_max_upload_mb}MB")
        try:
            return await service.ingest(file.filename, data)
        except UnsupportedFormat as e:
            raise HTTPException(status_code=400, detail=f"不支持的格式：{e}")
        except ParseError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except EmptyDocument:
            raise HTTPException(status_code=400, detail="文档为空或无法提取文本")

    @router.get("/api/documents")
    async def list_documents():
        return doc_store.list()

    @router.delete("/api/documents/{doc_id}")
    async def delete_document(doc_id: str):
        if service is None:
            raise HTTPException(status_code=503, detail="知识库未启用")
        if not doc_store.exists(doc_id):
            raise HTTPException(status_code=404, detail="文档不存在")
        service.delete(doc_id)
        return {"ok": True}

    return router

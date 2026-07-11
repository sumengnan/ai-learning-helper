# app/api/documents.py
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..auth import current_user
from ..knowledge import EmptyDocument
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

    @router.get("/api/documents")
    async def list_fragments(page: int = 1, size: int = 8,
                             user_id: str = Depends(current_user)):
        # 知识库以切分后的片段（chunk）为展示单元，每片一项
        if service is None:
            return {"items": [], "total": 0}
        size = max(1, min(100, size))
        return service.list_fragments(user_id, max(1, page), size)

    @router.get("/api/documents/search")
    async def search_fragments(q: str = "", k: int = 30,
                               user_id: str = Depends(current_user)):
        if service is None:
            raise HTTPException(status_code=503, detail="知识库未启用（未配置 embedding）")
        if not q.strip():
            return []
        return await service.search(user_id, q.strip(), k)

    @router.delete("/api/documents/{chunk_id}")
    async def delete_fragment(chunk_id: str, user_id: str = Depends(current_user)):
        if service is None:
            raise HTTPException(status_code=503, detail="知识库未启用")
        if not service.delete_fragment(user_id, chunk_id):
            raise HTTPException(status_code=404, detail="片段不存在")
        return {"ok": True}

    return router

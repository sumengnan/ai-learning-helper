# app/api/attachments.py
from __future__ import annotations

import mimetypes
import os

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..auth import current_user


def make_attachments_router(store, conv_store, config) -> APIRouter:
    router = APIRouter()

    @router.post("/api/conversations/{conv_id}/attachments")
    async def upload(conv_id: str, file: UploadFile = File(...),
                     user_id: str = Depends(current_user)):
        if not conv_store.exists(user_id, conv_id):
            raise HTTPException(status_code=404, detail="对话不存在")
        if store.count_conv(user_id, conv_id) >= config.attachment_max_count:
            raise HTTPException(status_code=400,
                                detail=f"附件最多 {config.attachment_max_count} 个")
        limit = config.attachment_max_mb * 1024 * 1024
        if file.size is not None and file.size > limit:  # 读入内存前先按声明大小拦截
            raise HTTPException(status_code=413, detail=f"文件超过 {config.attachment_max_mb}MB")
        data = await file.read()
        if len(data) > limit:  # 兜底：size 缺失或不实按实际字节再判
            raise HTTPException(status_code=413, detail=f"文件超过 {config.attachment_max_mb}MB")
        content_type = (file.content_type
                        or mimetypes.guess_type(file.filename or "")[0]
                        or "application/octet-stream")
        return store.create(user_id, conv_id, file.filename or "file", data, content_type)

    @router.get("/api/attachments/{aid}")
    async def get_attachment(aid: str, user_id: str = Depends(current_user)):
        rec = store.get(user_id, aid)
        # 登记在但磁盘文件缺失时也当 404，避免 FileResponse os.stat 抛 500
        if rec is None or not os.path.exists(rec["path"]):
            raise HTTPException(status_code=404, detail="文件不存在")
        return FileResponse(rec["path"], media_type=rec["content_type"],
                            filename=rec["filename"],
                            content_disposition_type="inline")  # 供浏览器原生预览

    @router.delete("/api/attachments/{aid}")
    async def delete_attachment(aid: str, user_id: str = Depends(current_user)):
        if not store.delete(user_id, aid):
            raise HTTPException(status_code=404, detail="文件不存在")
        return {"ok": True}

    return router

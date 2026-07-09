# app/api/downloads.py
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..auth import current_user


def make_downloads_router(download_store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/downloads")
    async def list_downloads(user_id: str = Depends(current_user)):
        return download_store.list(user_id)

    @router.get("/api/downloads/{did}")
    async def get_download(did: str, user_id: str = Depends(current_user)):
        rec = download_store.get(user_id, did)
        # 登记在但磁盘文件缺失（被外部删）时也当 404，避免 FileResponse os.stat 抛 500
        if rec is None or not os.path.exists(rec["path"]):
            raise HTTPException(status_code=404, detail="文件不存在")
        return FileResponse(rec["path"], media_type=rec["content_type"],
                            filename=rec["filename"])

    @router.delete("/api/downloads/{did}")
    async def delete_download(did: str, user_id: str = Depends(current_user)):
        if not download_store.delete(user_id, did):
            raise HTTPException(status_code=404, detail="文件不存在")
        return {"ok": True}

    return router

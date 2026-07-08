# app/api/downloads.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


def make_downloads_router(download_store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/downloads")
    async def list_downloads():
        return download_store.list()

    @router.get("/api/downloads/{did}")
    async def get_download(did: str):
        rec = download_store.get(did)
        if rec is None:
            raise HTTPException(status_code=404, detail="文件不存在")
        return FileResponse(rec["path"], media_type=rec["content_type"],
                            filename=rec["filename"])

    @router.delete("/api/downloads/{did}")
    async def delete_download(did: str):
        if not download_store.delete(did):
            raise HTTPException(status_code=404, detail="文件不存在")
        return {"ok": True}

    return router

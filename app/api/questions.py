# app/api/questions.py
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from ..auth import current_user
from ..parsing import ParseError, UnsupportedFormat, parse_file


class IdsBody(BaseModel):
    ids: list[str]


def make_questions_router(question_store, config, question_importer=None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/questions")
    async def list_questions(page: int = 1, size: int = 20, type: str = "",
                             source: str = "", q: str = "",
                             user_id: str = Depends(current_user)):
        size = max(1, min(100, size))
        page = max(1, page)
        kw = dict(type=type or None, source=source or None, q=q.strip() or None)
        items = question_store.list(user_id, limit=size, offset=(page - 1) * size, **kw)
        return {"items": items, "total": question_store.count(user_id, **kw)}

    @router.get("/api/questions/sources")
    async def question_sources(user_id: str = Depends(current_user)):
        return question_store.sources(user_id)

    @router.post("/api/questions/import")
    async def import_questions(file: UploadFile = File(...),
                               user_id: str = Depends(current_user)):
        if question_importer is None:
            raise HTTPException(status_code=503, detail="导入未启用")
        limit = config.app_max_upload_mb * 1024 * 1024
        if file.size is not None and file.size > limit:
            raise HTTPException(status_code=413, detail=f"文件超过 {config.app_max_upload_mb}MB")
        data = await file.read()
        if len(data) > limit:
            raise HTTPException(status_code=413, detail=f"文件超过 {config.app_max_upload_mb}MB")
        try:
            text = parse_file(file.filename, data)
        except UnsupportedFormat as e:
            raise HTTPException(status_code=400, detail=f"不支持的格式：{e}")
        except ParseError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if not text.strip():
            raise HTTPException(status_code=400, detail="文件为空或无法提取文本")
        return await question_importer.import_text(user_id, file.filename, text)

    @router.delete("/api/questions/{qid}")
    async def delete_question(qid: str, user_id: str = Depends(current_user)):
        question_store.delete(user_id, qid)
        return {"ok": True}

    @router.post("/api/questions/delete")
    async def delete_questions(body: IdsBody, user_id: str = Depends(current_user)):
        question_store.delete_many(user_id, body.ids)
        return {"ok": True}

    return router

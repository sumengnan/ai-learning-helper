# app/api/questions.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..quiz_service import NoKnowledge, QuizError


class GenerateBody(BaseModel):
    topic: str
    count: int = 5
    types: list[str] = ["single"]


class IdsBody(BaseModel):
    ids: list[str]


def make_questions_router(quiz_service, question_store, config) -> APIRouter:
    router = APIRouter()

    @router.post("/api/questions/generate")
    async def generate(body: GenerateBody, user_id: str = Depends(current_user)):
        if quiz_service is None:
            raise HTTPException(status_code=503, detail="知识库未启用（未配置 embedding）")
        count = max(1, min(body.count, config.quiz_max_count))
        types = body.types or ["single"]
        try:
            questions = await quiz_service.generate(user_id, body.topic, count, types)
        except NoKnowledge:
            raise HTTPException(status_code=422, detail="知识库无相关内容，请先在知识库上传资料")
        except QuizError as e:
            raise HTTPException(status_code=502, detail=f"出题失败：{e}")
        return {"questions": questions}

    @router.get("/api/questions")
    async def list_questions(user_id: str = Depends(current_user)):
        return question_store.list(user_id)

    @router.delete("/api/questions/{qid}")
    async def delete_question(qid: str, user_id: str = Depends(current_user)):
        question_store.delete(user_id, qid)
        return {"ok": True}

    @router.post("/api/questions/delete")
    async def delete_questions(body: IdsBody, user_id: str = Depends(current_user)):
        question_store.delete_many(user_id, body.ids)
        return {"ok": True}

    return router

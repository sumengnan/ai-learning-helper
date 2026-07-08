# app/api/exams.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class ComposeBody(BaseModel):
    count: int = 5
    types: list[str] | None = None


class Answer(BaseModel):
    question_id: str
    user_answer: object = None


class SubmitBody(BaseModel):
    answers: list[Answer]


class IdsBody(BaseModel):
    ids: list[str]


def _snapshot(q: dict) -> dict:
    return {"type": q["type"], "stem": q["stem"], "options": q["options"],
            "answer": q["answer"], "explanation": q["explanation"]}


def make_exams_router(quiz_service, question_store, exam_store, wrong_store) -> APIRouter:
    router = APIRouter()

    @router.post("/api/exams")
    async def compose(body: ComposeBody):
        picked = question_store.sample(body.count, body.types)
        # 去掉 answer/explanation，防前端偷看
        paper = [{"id": q["id"], "type": q["type"], "stem": q["stem"],
                  "options": q["options"]} for q in picked]
        return {"questions": paper}

    @router.post("/api/exams/submit")
    async def submit(body: SubmitBody):
        if quiz_service is None:
            raise HTTPException(status_code=503, detail="知识库未启用")
        detail, correct = [], 0
        graded = []  # (question, user_answer, result)
        for ans in body.answers:
            q = question_store.get(ans.question_id)
            if q is None:
                detail.append({"question_id": ans.question_id, "missing": True,
                               "correct": False})
                continue
            res = await quiz_service.grade(q, ans.user_answer)
            if res["correct"]:
                correct += 1
            detail.append({
                "question_id": q["id"], "type": q["type"], "stem": q["stem"],
                "user_answer": ans.user_answer, "correct": res["correct"],
                "correct_answer": q["answer"], "explanation": q["explanation"],
                "feedback": res.get("feedback")})
            graded.append((q, ans.user_answer, res))
        total = len(body.answers)
        score = round(correct / total * 100, 1) if total else 0.0
        exam_id = exam_store.create(total, correct, score, detail)
        for q, ua, res in graded:
            if not res["correct"]:
                wrong_store.create(q["id"], exam_id, _snapshot(q), ua)
        return {"exam_id": exam_id, "total": total, "correct": correct,
                "score": score, "detail": detail}

    @router.get("/api/exams")
    async def list_exams():
        return exam_store.list()

    @router.get("/api/wrong-answers")
    async def list_wrong():
        return wrong_store.list()

    @router.post("/api/wrong-answers/delete")
    async def delete_wrong(body: IdsBody):
        wrong_store.delete_many(body.ids)
        return {"ok": True}

    @router.delete("/api/wrong-answers/{wid}")
    async def delete_one_wrong(wid: str):
        wrong_store.delete(wid)
        return {"ok": True}

    return router

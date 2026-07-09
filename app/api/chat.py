# app/api/chat.py
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from harness.events import RunError, RunFinished
from harness.loop.agent_loop import AgentLoop
from harness.persistence.serialize import event_to_dict
from harness.reliability.budget import BudgetTracker
from harness.tools.base import ToolRegistry
from harness.types import Message, Role

from ..auth import current_user
from ..context import ConversationContextManager
from ..tools.exam_tools import SampleQuestionsTool, SaveWrongAnswerTool
from ..tools.save_download import SaveDownloadTool

EXAM_GUIDE = (
    "\n\n你具备「模拟考试」能力：\n"
    "- 当用户想模拟考试/刷题时，用 sample_questions 工具从其题库抽题。\n"
    "- 开始前先询问用户选择哪种模式：\n"
    "  1) 打分式：逐题作答，全部答完后统一给出得分与逐题讲解；作答过程中不要提前公布答案。\n"
    "  2) 即时式：每答一题立即给出正确答案与解析，不计分，直到用户说「结束」。\n"
    "- 每次只问一道题，等用户作答后再继续。\n"
    "- 客观题（单选/多选/判断）依据题目答案判定对错；简答题结合参考答案判断。\n"
    "- 若某题用户答错且 save_wrong_answer 工具可用，则调用它把该题存入错题集"
    "（传 question_id 与用户作答 user_answer）；若该工具不可用，说明「答错自动保存错题集」"
    "未开启，不要尝试保存。\n")


class _ChatRequest(BaseModel):
    conversation_id: str
    message: str
    save_wrong: bool = False        # 「考试答错自动保存错题集」开关（默认关）


def make_chat_router(harness, store, config, question_store=None, wrong_store=None) -> APIRouter:
    router = APIRouter()

    def _build_registry(user_id: str, save_wrong: bool) -> ToolRegistry:
        reg = ToolRegistry()
        for t in harness.registry.tools():
            reg.register(t)
        # 用用户级 save_download 覆盖全局那个（同名），使下载文件按用户隔离
        dstore = getattr(harness, "download_store", None)
        if dstore is not None:
            reg.register(SaveDownloadTool(
                dstore, config.download_max_mb * 1024 * 1024, user_id))
        if question_store is not None:
            reg.register(SampleQuestionsTool(question_store, user_id))
            if save_wrong and wrong_store is not None:
                reg.register(SaveWrongAnswerTool(question_store, wrong_store, user_id))
        return reg

    @router.post("/api/chat")
    async def chat(req: _ChatRequest, user_id: str = Depends(current_user)):
        if not store.exists(user_id, req.conversation_id):
            raise HTTPException(status_code=404, detail="对话不存在")
        history = store.messages(req.conversation_id)
        ctx = ConversationContextManager(harness.system_prompt + EXAM_GUIDE, history)
        registry = _build_registry(user_id, req.save_wrong)
        loop = AgentLoop(
            client=harness.client, registry=registry, context=ctx,
            max_steps=config.max_steps,
            budget=BudgetTracker(config.max_tokens_budget, config.max_wall_seconds),
            checkpoint_store=harness.checkpoint_store, model_name=config.model,
            price_map=config.price_map, tool_result_max_chars=config.tool_result_max_chars)

        async def gen():
            final = None
            try:
                async for ev in harness.sink.wrap(loop.run(req.message)):
                    if isinstance(ev, RunFinished):
                        final = ev.message.content
                    elif isinstance(ev, RunError):
                        final = final or "（本轮未能完成，请重试）"
                    yield f"data: {json.dumps(event_to_dict(ev), ensure_ascii=False)}\n\n"
            finally:
                # 客户端断开（GeneratorExit）或异常时仍落库，避免本轮用户消息丢失
                store.append(req.conversation_id, [
                    Message(role=Role.USER, content=req.message),
                    Message(role=Role.ASSISTANT, content=final or "（本轮未完成）")])

        return StreamingResponse(gen(), media_type="text/event-stream")

    return router

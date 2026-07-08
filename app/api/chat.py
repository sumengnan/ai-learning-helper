# app/api/chat.py
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from harness.events import RunError, RunFinished
from harness.loop.agent_loop import AgentLoop
from harness.persistence.serialize import event_to_dict
from harness.reliability.budget import BudgetTracker
from harness.types import Message, Role

from ..context import ConversationContextManager


class _ChatRequest(BaseModel):
    conversation_id: str
    message: str


def make_chat_router(harness, store, config) -> APIRouter:
    router = APIRouter()

    @router.post("/api/chat")
    async def chat(req: _ChatRequest):
        if not store.exists(req.conversation_id):
            raise HTTPException(status_code=404, detail="对话不存在")
        history = store.messages(req.conversation_id)
        ctx = ConversationContextManager(harness.system_prompt, history)
        loop = AgentLoop(
            client=harness.client, registry=harness.registry, context=ctx,
            max_steps=config.max_steps,
            budget=BudgetTracker(config.max_tokens_budget, config.max_wall_seconds),
            checkpoint_store=harness.checkpoint_store, model_name=config.model,
            price_map=config.price_map, tool_result_max_chars=config.tool_result_max_chars)

        async def gen():
            final = None
            async for ev in harness.sink.wrap(loop.run(req.message)):
                if isinstance(ev, RunFinished):
                    final = ev.message.content
                elif isinstance(ev, RunError):
                    final = final or f"[出错] {ev.error}"
                yield f"data: {json.dumps(event_to_dict(ev), ensure_ascii=False)}\n\n"
            store.append(req.conversation_id, [
                Message(role=Role.USER, content=req.message),
                Message(role=Role.ASSISTANT, content=final or ""),
            ])

        return StreamingResponse(gen(), media_type="text/event-stream")

    return router

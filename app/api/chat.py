# app/api/chat.py
from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from harness.approval import reset_context, resolve, set_context
from harness.events import Progress, RunError, RunFinished, TextDelta, ToolFinished, ToolStarted
from harness.loop.agent_loop import AgentLoop
from harness.persistence.serialize import event_to_dict
from harness.progress import reset_emitter, set_emitter
from harness.reliability.budget import BudgetTracker
from harness.tools.base import ToolRegistry
from harness.types import Message, Role

from ..auth import current_user
from ..context import ConversationContextManager
from ..sandbox_manager import reset_sandbox_conv, set_sandbox_conv
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


def _final_content(final: str | None, partial: str) -> str:
    """本轮落库的助手内容：正常完成用 final；用户中途停止（无 final）时保留已生成的
    部分文本并标注「已停止」，什么都没生成则用占位符。"""
    if final:
        return final
    partial = partial.strip()
    return f"{partial}\n\n（已停止）" if partial else "（本轮已停止）"


class _ChatRequest(BaseModel):
    conversation_id: str
    message: str
    save_wrong: bool = False        # 「考试答错自动保存错题集」开关（默认关）


class _Decision(BaseModel):
    approval_id: str
    approved: bool


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
        if getattr(harness, "skill_registry", None) is not None:
            from harness.skills.context import SkillContextManager
            ctx = SkillContextManager(ctx, harness.skill_registry)
        registry = _build_registry(user_id, req.save_wrong)
        # 固定本轮 run_id 并登记到会话，使检查点/轨迹可在删除会话时按会话清理。
        # 提前落库：即使本轮报错，遗留的轨迹也能被后续删除清掉。
        run_id = uuid4().hex
        store.add_run(req.conversation_id, run_id)
        loop = AgentLoop(
            client=harness.client, registry=registry, context=ctx,
            max_steps=config.max_steps, run_id_factory=lambda: run_id,
            budget=BudgetTracker(config.max_tokens_budget, config.max_wall_seconds),
            checkpoint_store=harness.checkpoint_store, model_name=config.model,
            price_map=config.price_map, tool_result_max_chars=config.tool_result_max_chars)

        async def gen():
            final = None
            # 流式文本增量的累积：用户中途停止时用它保留已生成的部分回答
            partial: list[str] = []
            # 工具调用轨迹（纯 UI 用途），随助手消息落库，切换对话回来后仍可还原
            steps: list[dict] = []
            step_by_id: dict[str, dict] = {}
            # 沙箱/子代理执行进度轨迹，同样落库，刷新/切回对话后仍可还原
            progress: list[dict] = []
            # 工具执行中的进度事件（沙箱初始化 / 子 agent 派发）经 emitter 并入同一队列
            queue: asyncio.Queue = asyncio.Queue()
            sentinel = object()

            async def pump():
                token = set_emitter(queue.put_nowait)
                # 审批上下文：填入本轮真实 run_id；超时来自配置
                atoken = set_context(run_id=run_id, timeout=config.sandbox_approval_timeout)
                # 会话级沙箱上下文：工具执行在此上下文内解析出本会话的容器
                stoken = set_sandbox_conv(req.conversation_id)
                try:
                    async for ev in harness.sink.wrap(loop.run(req.message)):
                        queue.put_nowait(ev)
                except Exception as e:  # 兜底成 RunError，避免流卡死
                    queue.put_nowait(RunError(error=str(e)))
                finally:
                    reset_sandbox_conv(stoken)
                    reset_context(atoken)
                    reset_emitter(token)
                    queue.put_nowait(sentinel)

            task = asyncio.create_task(pump())
            try:
                while True:
                    ev = await queue.get()
                    if ev is sentinel:
                        break
                    if isinstance(ev, RunFinished):
                        final = ev.message.content
                    elif isinstance(ev, RunError):
                        final = final or "（本轮未能完成，请重试）"
                    elif isinstance(ev, TextDelta):
                        partial.append(ev.text)
                    elif isinstance(ev, ToolStarted):
                        tc = ev.tool_call
                        st = {"tool": tc.name, "args": tc.arguments}
                        steps.append(st)
                        step_by_id[tc.id] = st
                    elif isinstance(ev, ToolFinished):
                        st = step_by_id.get(ev.result.tool_call_id)
                        if st is not None:
                            st["result"] = ev.result.content
                            st["is_error"] = ev.result.is_error
                    elif isinstance(ev, Progress):
                        progress.append({"scope": ev.scope, "text": ev.text,
                                         "status": ev.status, "key": ev.key})
                    yield f"data: {json.dumps(event_to_dict(ev), ensure_ascii=False)}\n\n"
            finally:
                # 客户端断开（GeneratorExit）或异常时仍落库，避免本轮用户消息丢失
                if not task.done():
                    task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                store.append(req.conversation_id, [
                    Message(role=Role.USER, content=req.message),
                    Message(role=Role.ASSISTANT,
                            content=_final_content(final, "".join(partial)))],
                    steps=steps or None, progress=progress or None)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @router.post("/api/chat/{run_id}/decision")
    async def decision(run_id: str, body: _Decision, user_id: str = Depends(current_user)):
        # run_id 用于 REST 语义/审计；实际解析按全局唯一的 approval_id
        if not resolve(body.approval_id, body.approved):
            raise HTTPException(status_code=404, detail="审批已失效或不存在")
        return {"ok": True}

    return router

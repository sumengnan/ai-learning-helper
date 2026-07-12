# app/api/chat.py
from __future__ import annotations

import asyncio
import json
import time
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from harness.approval import reset_context, resolve, set_context
from harness.events import (
    ModelUsage, Progress, RunError, RunFinished, TextDelta, ToolFinished, ToolStarted)
from harness.loop.agent_loop import AgentLoop
from harness.persistence.serialize import event_to_dict
from harness.progress import reset_emitter, set_emitter
from harness.reliability.budget import BudgetTracker
from harness.tools.base import ToolRegistry
from harness.types import Message, Role

from ..auth import current_user
from ..completion import build_completer
from ..context_assembly import ContextAssembler
from ..conversation_memory import ConversationMemoryService
from ..sandbox_manager import reset_sandbox_conv, set_sandbox_conv
from ..summaries import SummaryStore
from ..summarizer import RollingSummarizer
from ..sources import SOURCE_GUIDE, SourceSink, wrap_tool
from ..tools.attachment_tools import ListAttachmentsTool, ReadAttachmentTool
from ..tools.exam_tools import SampleQuestionsTool, SaveWrongAnswerTool
from ..tools.save_download import SaveDownloadTool
from ..verify import Verdict

# 交付门缓冲后补发终稿时，把文本切成小片以保留打字机效果
_DELIVER_CHUNK = 40


def _chunks(text: str, size: int = _DELIVER_CHUNK):
    for i in range(0, len(text), size):
        yield text[i:i + size]

EXAM_GUIDE = (
    "\n\n你具备「模拟考试」能力：\n"
    "- 当用户想模拟考试/刷题时，用 sample_questions 工具从其题库抽题。\n"
    "- 默认采用「即时式」，无需询问模式，直接开始：每答一题立即给出正确答案与解析，"
    "不计分，直到用户说「结束」。\n"
    "- 仅当用户明确要求「打分」「计分」「打分式」等时才改用「打分式」："
    "逐题作答，全部答完后统一给出得分与逐题讲解；作答过程中不要提前公布答案。\n"
    "- 每次只问一道题，等用户作答后再继续。\n"
    "- 客观题（单选/多选/判断）依据题目答案判定对错；简答题结合参考答案判断。\n"
    "- 若某题用户答错且 save_wrong_answer 工具可用，则调用它把该题存入错题集"
    "（传 question_id 与用户作答 user_answer）；若该工具不可用，说明「答错自动保存错题集」"
    "未开启，不要尝试保存。\n")

ATTACHMENT_GUIDE = (
    "\n\n用户可能在消息中上传附件（文件内容默认不在上下文里，需要时再取）：\n"
    "- 用 list_attachments 查看本对话的附件清单（id/文件名/类型）。\n"
    "- 用 read_attachment(attachment_id) 读取具体内容：txt/pdf/word 返回文本，图片作为视觉加载。\n"
    "- 所有附件也已放入沙箱 /workspace/uploads/，可用 run_python/run_shell 直接读取或执行。\n"
    "- 只在确有需要时才读取附件，不要无谓地逐个打开。\n")


class _ChatRequest(BaseModel):
    conversation_id: str
    message: str
    save_wrong: bool = False        # 「考试答错自动保存错题集」开关（默认关）
    attachment_ids: list[str] = []  # 本轮随消息发送的附件（已先经上传接口拿到 id）


class _Decision(BaseModel):
    approval_id: str
    approved: bool


def make_chat_router(harness, store, config, question_store=None, wrong_store=None,
                     verifier=None, attachment_store=None, run_manager=None) -> APIRouter:
    router = APIRouter()
    # 断点续传：一轮生成跑成脱离请求的后台任务，事件走 RunManager 内存总线（见 app/run_manager.py）。
    # 未注入时退化为每路由独立实例（测试/无续传场景），行为仍正确、只是跨请求接不上。
    if run_manager is None:
        from ..run_manager import RunManager
        run_manager = RunManager()

    # 分层上下文：full（默认）时不构造摘要/检索依赖，行为与历史一致。
    _strategy = getattr(config, "context_strategy", "full")
    _summarizer = _conv_memory = None
    if _strategy == "layered":
        if getattr(config, "context_enable_summary", True):
            _summarizer = RollingSummarizer(
                SummaryStore(conn=store._conn),           # 复用 app.db 连接
                build_completer(harness.client, config.model),
                model=config.model,
                max_summary_tokens=config.context_summary_max_tokens)
        if getattr(config, "context_enable_retrieval", True) and \
                getattr(harness, "memory", None) is not None:
            _conv_memory = ConversationMemoryService(
                harness.memory,
                writer=getattr(harness, "memory_writer", None),
                sample_rate=config.memory_write_sample_rate)
    _assembler = ContextAssembler(config, config.model,
                                  summarizer=_summarizer, conv_memory=_conv_memory)

    def _build_registry(user_id: str, save_wrong: bool, conv_id: str,
                        has_attachments: bool) -> tuple[ToolRegistry, SourceSink]:
        reg = ToolRegistry()
        sink = SourceSink()
        # 来源工具包一层记源（wrap_tool 只包「产生来源」的工具，其余原样）
        def _reg(t):
            reg.register(wrap_tool(t, sink))
        for t in harness.registry.tools():
            _reg(t)
        # 用用户级 save_download 覆盖全局那个（同名），使下载文件按用户隔离
        dstore = getattr(harness, "download_store", None)
        if dstore is not None:
            _reg(SaveDownloadTool(
                dstore, config.download_max_mb * 1024 * 1024, user_id))
        if question_store is not None:
            _reg(SampleQuestionsTool(question_store, user_id))
            if save_wrong and wrong_store is not None:
                _reg(SaveWrongAnswerTool(question_store, wrong_store, user_id))
        # 附件工具：本会话有过附件才暴露（按需取内容，图片走视觉）
        if attachment_store is not None and has_attachments:
            _reg(ListAttachmentsTool(attachment_store, user_id, conv_id))
            _reg(ReadAttachmentTool(
                attachment_store, user_id, conv_id,
                config.attachment_vision_max_mb * 1024 * 1024))
        return reg, sink

    def _sse(ev) -> str:
        return f"data: {json.dumps(event_to_dict(ev), ensure_ascii=False)}\n\n"

    @router.post("/api/chat")
    async def chat(req: _ChatRequest, user_id: str = Depends(current_user)):
        if not store.exists(user_id, req.conversation_id):
            raise HTTPException(status_code=404, detail="对话不存在")
        # 本轮随消息发送的附件：校验归属并取元数据
        attachment_metas: list[dict] = []
        if req.attachment_ids and attachment_store is not None:
            for aid in req.attachment_ids:
                rec = attachment_store.get(user_id, aid)
                if rec is None:
                    raise HTTPException(status_code=404, detail=f"附件不存在：{aid}")
                attachment_metas.append(
                    {k: rec[k] for k in ("id", "filename", "size", "content_type")})
        # 会话是否曾有附件（决定是否暴露附件工具）：本轮有，或历史有
        has_attachments = bool(attachment_metas) or (
            attachment_store is not None
            and attachment_store.count_conv(user_id, req.conversation_id) > 0)
        history = store.messages(req.conversation_id)
        registry, source_sink = _build_registry(user_id, req.save_wrong,
                                                 req.conversation_id, has_attachments)
        gate_on = verifier is not None and config.enable_answer_gate
        # 喂给模型的消息：带附件时追加只含文件名的名单提示（不含内容），入库仍用原文
        model_message = req.message
        if attachment_metas:
            names = "、".join(m["filename"] for m in attachment_metas)
            model_message = (
                f"{req.message}\n\n[本轮附件] 用户上传了 {len(attachment_metas)} 个文件："
                f"{names}（内容未加载，需要时用 list_attachments/read_attachment 获取，"
                f"或在沙箱 /workspace/uploads/ 下访问）")

        # 分层上下文：窗口/摘要/检索的重活在此按会话级预算一次，_new_loop 内只做同步拼装。
        base_ctx = await _assembler.build_manager(
            harness.system_prompt + EXAM_GUIDE + ATTACHMENT_GUIDE + SOURCE_GUIDE, history,
            req.message, req.conversation_id)

        def _new_loop(run_id_a: str) -> AgentLoop:
            ctx = base_ctx
            if getattr(harness, "skill_registry", None) is not None:
                from harness.skills.context import SkillContextManager
                ctx = SkillContextManager(ctx, harness.skill_registry)
            return AgentLoop(
                client=harness.client, registry=registry, context=ctx,
                max_steps=config.max_steps, run_id_factory=lambda: run_id_a,
                budget=BudgetTracker(config.max_tokens_budget, config.max_wall_seconds),
                checkpoint_store=harness.checkpoint_store, model_name=config.model,
                price_map=config.price_map, tool_result_max_chars=config.tool_result_max_chars)

        async def _drain(loop_obj, run_id_a, message, passthrough, collect):
            """跑一次 AgentLoop，逐事件产出 SSE 串；把 final/steps/grounding 收进 collect。

            passthrough=False（交付门）时缓冲不转发终态事件（TextDelta/RunFinished/RunError），
            仅转发"agent 在干活"的活动事件；终稿由调用方校验后再补发。
            """
            queue: asyncio.Queue = asyncio.Queue()
            sentinel = object()
            step_by_id: dict[str, dict] = {}

            async def pump():
                token = set_emitter(queue.put_nowait)
                atoken = set_context(run_id=run_id_a, timeout=config.sandbox_approval_timeout)
                stoken = set_sandbox_conv(req.conversation_id)
                try:
                    # 本轮附件播种进会话沙箱 /workspace/uploads/，供模型直接执行（写盘≠给模型）
                    if attachment_metas and harness.sandbox is not None:
                        for meta in attachment_metas:
                            try:
                                data = attachment_store.bytes(meta["id"])
                                await harness.sandbox.write_bytes(
                                    f"uploads/{meta['filename']}", data)
                            except Exception as e:  # 播种失败不应打断本轮对话
                                queue.put_nowait(Progress(
                                    scope="sandbox",
                                    text=f"附件 {meta['filename']} 载入沙箱失败：{e}",
                                    status="error"))
                    async for ev in harness.sink.wrap(loop_obj.run(message)):
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
                        collect["final"] = ev.message.content
                    elif isinstance(ev, RunError):
                        collect["error"] = ev.error
                    elif isinstance(ev, ToolStarted):
                        tc = ev.tool_call
                        st = {"tool": tc.name, "args": tc.arguments}
                        collect["steps"].append(st)
                        step_by_id[tc.id] = st
                    elif isinstance(ev, ToolFinished):
                        st = step_by_id.get(ev.result.tool_call_id)
                        if st is not None:
                            st["result"] = ev.result.content
                            st["is_error"] = ev.result.is_error
                            if st["tool"] in ("search_memory", "run_python", "run_node", "run_java"):
                                collect["grounding"].append(
                                    {"tool": st["tool"], "content": ev.result.content,
                                     "is_error": ev.result.is_error})
                    elif isinstance(ev, ModelUsage):
                        # 记录用量供落库（与前端一致取最新一次的 total/cost），刷新后仍可展示
                        collect["usage"] = {"tokens": ev.usage.total_tokens, "cost": ev.cost_usd}
                    elif isinstance(ev, Progress):
                        collect["progress"].append({"scope": ev.scope, "text": ev.text,
                                                    "status": ev.status, "key": ev.key,
                                                    "agent": ev.agent})
                    # 交付门下缓冲终态事件（不转发）；直通模式转发全部
                    if passthrough or not isinstance(ev, (TextDelta, RunFinished, RunError)):
                        yield ev
            finally:
                if not task.done():
                    task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        async def gen(turn_run_id: str):
            """产出 Event 对象的生成器，由 RunManager 后台驱动（不随请求取消）。
            结束时按 turn_run_id 把 streaming 占位消息 UPDATE 为最终态。"""
            steps: list[dict] = []       # 工具调用轨迹（落库供 UI 还原）
            progress: list[dict] = []    # 沙箱/子代理/校验进度（落库）
            turn_start = time.time()     # 本轮墙钟起点，用于落库耗时（刷新后仍可展示）
            question = req.message
            delivered = None
            delivered_sources: list[dict] = []   # 交付那次尝试的权威来源
            errored = False
            parts: list[str] = []        # 累积客户端面 TextDelta，供 stop/重启保留已生成部分

            def _acc(ev):
                if isinstance(ev, TextDelta):
                    parts.append(ev.text)
                    if len(parts) % 25 == 0:   # 去抖 flush：仅为服务重启后能看到断点前部分
                        store.flush_partial(req.conversation_id, turn_run_id, "".join(parts))
                return ev

            def _emit_verify(text, status=None, key=None):
                ev = Progress("verify", text, status=status, key=key)
                progress.append({"scope": ev.scope, "text": ev.text,
                                 "status": ev.status, "key": ev.key, "agent": ev.agent})
                return ev

            def _emit_sources(items):
                # 借 Progress 通道把来源即时推给在途客户端（scope=sources，前端特判、不入 progress 列）
                return Progress("sources", json.dumps(items, ensure_ascii=False))

            try:
                if not gate_on:
                    # 直通路径：单次尝试、逐字流式（与开门前行为一致）
                    collect = {"final": None, "error": None, "steps": steps,
                               "grounding": [], "progress": progress, "usage": None}
                    run_id_a = uuid4().hex
                    store.add_run(req.conversation_id, run_id_a)
                    source_sink.reset()
                    async for s in _drain(_new_loop(run_id_a), run_id_a, model_message, True, collect):
                        yield _acc(s)
                    delivered = collect["final"] or (
                        "（本轮未能完成，请重试）" if collect["error"] else "（本轮未完成）")
                    errored = collect["final"] is None
                    delivered_sources = source_sink.snapshot()
                else:
                    # 交付门：缓冲 → 校验 → 不过则回灌重答，最多 answer_gate_max_retries 次
                    corrective = None
                    max_attempts = config.answer_gate_max_retries + 1
                    for attempt in range(max_attempts):
                        msg = model_message if corrective is None else corrective
                        collect = {"final": None, "error": None, "steps": [],
                                   "grounding": [], "progress": progress, "usage": None}
                        run_id_a = uuid4().hex
                        store.add_run(req.conversation_id, run_id_a)
                        source_sink.reset()   # 每次尝试重置，交付时快照该次来源
                        async for s in _drain(_new_loop(run_id_a), run_id_a, msg, False, collect):
                            yield s
                        steps.extend(collect["steps"])
                        draft = (collect["final"] or "").strip()

                        ekey = uuid4().hex
                        yield _emit_verify("校验中…", status="running", key=ekey)
                        if not draft:
                            verdict = Verdict(ok=False, failed=["empty"],
                                              critique=collect["error"] or "本轮未产出答案",
                                              summary="未产出答案")
                        else:
                            stoken = set_sandbox_conv(req.conversation_id)
                            try:
                                verdict = await verifier.verify(
                                    question, draft, collect["grounding"], registry)
                            finally:
                                reset_sandbox_conv(stoken)

                        if verdict.ok:
                            yield _emit_verify("校验通过", status="ok", key=ekey)
                            delivered = draft
                            delivered_sources = source_sink.snapshot()
                            break
                        yield _emit_verify(f"未通过（{verdict.summary}）", status="error", key=ekey)
                        if attempt == max_attempts - 1:      # 用尽次数 → 降级交付
                            delivered = draft or "（本轮未完成）"
                            delivered_sources = source_sink.snapshot()
                            errored = not draft
                            if verdict.summary:
                                delivered = (f"⚠️ 此回答未通过自动校验（{verdict.summary}），"
                                             f"请谨慎参考。\n\n" + delivered)
                            break
                        yield _emit_verify("重答中…", status="running")
                        corrective = (f"你上一版回答未通过自动校验。问题：{verdict.critique}。"
                                      f"请针对性修正后，重新完整回答原问题：{question}")

                    # 交付：终稿以 TextDelta 补发（保留打字机）+ 合成 RunFinished
                    delivered = delivered or "（本轮未完成）"
                    for chunk in _chunks(delivered):
                        yield _acc(TextDelta(text=chunk))
                    yield RunFinished(message=Message(role=Role.ASSISTANT, content=delivered))

                # 正常完成：先把来源推给在途客户端，再 UPDATE 占位消息为最终态
                if delivered_sources:
                    yield _emit_sources(delivered_sources)
                status = "error" if errored else "done"
                final_content = delivered or "".join(parts) or "（本轮未完成）"
                _usage = collect.get("usage") or {}
                store.finish_turn(req.conversation_id, turn_run_id, final_content,
                                  steps=steps or None, progress=progress or None,
                                  status=status, sources=delivered_sources or None,
                                  tokens=_usage.get("tokens"), cost=_usage.get("cost"),
                                  elapsed_ms=round((time.time() - turn_start) * 1000))
                # L3：把对话文本写入向量库供后续语义召回（best-effort，不阻断）
                if _conv_memory is not None and not errored and final_content:
                    try:
                        await _conv_memory.record_turn(
                            req.conversation_id, len(history),
                            f"用户：{req.message}\n助手：{final_content}")
                    except Exception:
                        pass
            except asyncio.CancelledError:
                # stop：落已生成部分 + stopped，再放行取消
                store.finish_turn(
                    req.conversation_id, turn_run_id,
                    delivered or "".join(parts) or "（已停止）",
                    steps=steps or None, progress=progress or None, status="stopped",
                    elapsed_ms=round((time.time() - turn_start) * 1000))
                raise
            except Exception as e:  # noqa: BLE001  意外异常也要把占位落成 error，别永远 streaming
                store.finish_turn(
                    req.conversation_id, turn_run_id,
                    delivered or "".join(parts) or "（本轮未能完成，请重试）",
                    steps=steps or None, progress=progress or None, status="error",
                    elapsed_ms=round((time.time() - turn_start) * 1000))
                yield RunError(error=str(e))

        turn_run_id = uuid4().hex
        # 并发守卫：同一会话已有在途 run → 拒绝（前端 busy 守卫也拦）
        if run_manager.active_run_for_conv(req.conversation_id):
            raise HTTPException(status_code=409, detail="上一轮还在进行中，请稍候")
        # 开头即落库：user 消息 + streaming 占位 assistant（带 turn_run_id），刷新后可据此接回
        store.start_turn(req.conversation_id,
                         Message(role=Role.USER, content=req.message),
                         turn_run_id, attachment_metas or None)
        store.add_run(req.conversation_id, turn_run_id)
        await run_manager.start(turn_run_id, req.conversation_id, gen(turn_run_id))

        async def sse_stream():
            async for ev in run_manager.subscribe(turn_run_id):
                yield _sse(ev)
        # X-Run-Id 让前端拿到本轮句柄（供 stop / 刷新后接回）
        return StreamingResponse(sse_stream(), media_type="text/event-stream",
                                 headers={"X-Run-Id": turn_run_id})

    @router.get("/api/chat/attach/{run_id}")
    async def attach(run_id: str, user_id: str = Depends(current_user)):
        """刷新后接回一个在途 run：回放已缓冲事件 + 实时后续，直到完成。"""
        conv = store.conv_of_run(run_id)
        if conv is None or not store.exists(user_id, conv):
            raise HTTPException(status_code=404, detail="run 不存在")
        if not run_manager.is_active(run_id):
            # 已结束或服务重启丢失 → 让前端改为重载消息（库里已是最终态）
            raise HTTPException(status_code=409, detail="run 已结束")

        async def sse_stream():
            async for ev in run_manager.subscribe(run_id):
                yield _sse(ev)
        return StreamingResponse(sse_stream(), media_type="text/event-stream")

    @router.post("/api/chat/stop/{run_id}")
    async def stop(run_id: str, user_id: str = Depends(current_user)):
        """停止一个在途 run：取消后台任务，落已生成部分 + status=stopped。"""
        conv = store.conv_of_run(run_id)
        if conv is None or not store.exists(user_id, conv):
            raise HTTPException(status_code=404, detail="run 不存在")
        return {"ok": run_manager.cancel(run_id)}

    @router.post("/api/chat/{run_id}/decision")
    async def decision(run_id: str, body: _Decision, user_id: str = Depends(current_user)):
        # run_id 用于 REST 语义/审计；实际解析按全局唯一的 approval_id
        if not resolve(body.approval_id, body.approved):
            raise HTTPException(status_code=404, detail="审批已失效或不存在")
        return {"ok": True}

    return router

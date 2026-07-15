# app/api/chat.py
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from harness.approval import reset_context, resolve, set_context
from harness.events import (
    ModelUsage, Progress, ReasoningDelta, RunError, RunFinished, TextDelta,
    ToolFinished, ToolStarted)
from harness.llm.openai_compat import set_extra_body_override
from harness.loop.agent_loop import AgentLoop
from harness.persistence.serialize import event_to_dict
from harness.progress import reset_emitter, set_emitter
from opentelemetry.trace import Status, StatusCode

from harness.reliability.budget import BudgetTracker
from harness.telemetry.tracer import get_tracer
from harness.tools.base import ToolRegistry
from harness.types import Message, Role

from ..auth import current_user
from ..completion import build_completer
from ..context_assembly import ContextAssembler
from ..conversation_memory import ConversationMemoryService
from ..profile import render_profile_block
from ..sandbox_manager import reset_sandbox_conv, set_sandbox_conv
from ..summaries import SummaryStore
from ..summarizer import RollingSummarizer
from ..sources import SOURCE_GUIDE, SourceSink, wrap_tool
from ..url_blocklist import guard_fetch_tool
from ..tools.attachment_tools import ListAttachmentsTool, ReadAttachmentTool
from ..exam_flow import grade_exam_turn
from ..tools.exam_tools import (
    AddQuestionsTool,
    DeleteQuestionsTool,
    DeleteWrongAnswersTool,
    GenerateQuestionsTool,
    ListQuestionsTool,
    SampleQuestionsTool,
    SampleWrongAnswersTool,
    SaveWrongAnswerTool,
    StartExamTool,
)
from ..tools.knowledge_tools import SaveToKnowledgeTool
from ..tools.save_download import SaveDownloadTool
from ..logging_setup import set_log_context
from ..verify import Verdict, _tool_exec_summary, failed_layers_zh

log = logging.getLogger("app.chat")

# 交付门缓冲后补发终稿时，把文本切成小片以保留打字机效果
_DELIVER_CHUNK = 40


def _chunks(text: str, size: int = _DELIVER_CHUNK):
    for i in range(0, len(text), size):
        yield text[i:i + size]

EXAM_GUIDE = (
    "\n\n你具备「题库 / 错题集 / 模拟考试」能力：\n"
    "- 【正式模拟考试优先用 start_exam 开考】：从题库(source=bank)/错题集(source=wrong)抽题，"
    "或即席出题(source=adhoc，需在 questions 传入含答案的题)。开考后每题的判分与「答错自动存错题集」"
    "都由系统在后台确定性完成——你【无需也不要】调用 save_wrong_answer，只需把系统返回的题目呈现给用户，"
    "并在系统给出「[考试系统判定]…」提示后据其讲解、再呈现系统给的下一题。\n"
    "- 以下是未用 start_exam 时的零散练习指引：\n"
    "- 当用户想模拟考试/刷题时，用 sample_questions 从题库抽题；"
    "想「用错题重考/复习错题」时用 sample_wrong_answers 从错题集抽题；题库为空时可即席出题。\n"
    "- 每次只问一道题，等用户作答后再继续。\n"
    "- 客观题（单选/多选/判断）依据题目答案判定对错；简答题结合参考答案判断。\n"
    "- 默认采用「即时式」，无需询问模式，直接开始。每当用户答完一题，立刻判定对错并按下面处理：\n"
    "  · 若【答错】：先调用 save_wrong_answer 把这道题存入错题集，看到成功结果后，再向用户"
    "给出正确答案与解析。这一步是本流程的固定环节，不可跳过、不要等用户要求。\n"
    "  · 若答对：直接给出确认与简要解析。\n"
    "  全程不计分，直到用户说「结束」。\n"
    "- 仅当用户明确要求「打分」「计分」「打分式」等时才改用「打分式」：逐题作答、作答过程中不提前"
    "公布答案；全部答完后统一给出得分与逐题讲解，并对其中每道答错的题调用 save_wrong_answer 存入错题集。\n"
    "- save_wrong_answer 的调用方式：即席出题（题目不在题库）时【必须】直接传该题的 "
    "stem/type/answer（可含 options）以及用户作答 user_answer，不要只传 question_id；"
    "若该题来自 sample_questions，也可传其 question_id。\n"
    "- 【务必真的调用该工具，不能只是嘴上说】：用户每答错一道题就必须存入错题集，这是无条件的，"
    "不看用户是否要求、不问用户是否需要、也没有任何开关可以跳过。在你实际调用 save_wrong_answer "
    "并看到成功结果之前，绝不能说「已存入错题集」「已加入错题集」「已保存」之类的话——"
    "没有发起工具调用就一个字都不要提保存，那是欺骗用户。"
    "保存成功后，【务必明确告诉用户】「这道题已加入你的错题集，方便以后复习」，让用户清楚知道，不要含糊略过。\n"
    "\n题库管理：\n"
    "- 用户让你「把这些知识/资料整理成题存进题库」时，用 add_questions 直接把你整理好的"
    "题目写入题库；若用户希望「就某主题从我的知识库出题」，用 generate_questions（依赖知识库检索）。\n"
    "- 用 list_questions 查看题库（含 id），用 sample_wrong_answers 查看错题（含 id）。\n"
    "- delete_questions（删题库题）和 delete_wrong_answers（删错题）会永久删除，"
    "调用前必须先向用户复述将删除的具体题目并等待用户确认，切勿在未确认时直接删除。\n")

ATTACHMENT_GUIDE = (
    "\n\n用户可能在消息中上传附件（文件内容默认不在上下文里，需要时再取）：\n"
    "- 用 list_attachments 查看本对话的附件清单（id/文件名/类型）。\n"
    "- 用 read_attachment(attachment_id) 读取具体内容：txt/pdf/word 返回文本，图片作为视觉加载。\n"
    "- 所有附件也已放入沙箱 /workspace/uploads/，可用 run_python/run_shell 直接读取或执行。\n"
    "- 只在确有需要时才读取附件，不要无谓地逐个打开。\n")

# 北京时间（东八区）：本应用面向中文用户，用它作为「今天」的基准
_CN_TZ = timezone(timedelta(hours=8))


def _today_guide() -> str:
    """每请求注入当前日期，避免模型沿用训练数据里的年份做时间推算（如把「未来3年」从旧年份起算）。"""
    now = datetime.now(_CN_TZ)
    return (f"\n\n【当前日期】今天是 {now:%Y 年 %m 月 %d 日}（北京时间）。"
            "凡涉及「今年/去年/未来 N 年/最近/最新」等与时间相关的推算或表述，"
            "一律以此日期为当前时间基准，不要沿用训练数据中的年份。")


class _ChatRequest(BaseModel):
    conversation_id: str
    message: str
    think: bool = True              # 「思考模式」开关（默认开）；透传 enable_thinking，可手动关
    verify: bool = True             # 「结果校验」开关（默认开）；关则本轮跳过交付门校验
    attachment_ids: list[str] = []  # 本轮随消息发送的附件（已先经上传接口拿到 id）


class _Decision(BaseModel):
    approval_id: str
    approved: bool


_DL_ID_RE = re.compile(r"〔下载ID:([^〕]+)〕")
_KB_ID_RE = re.compile(r"〔知识ID:([^〕]+)〕")
_Q_ID_RE = re.compile(r"〔题目ID:([^〕]+)〕")


def _side_effect_ids(steps: list[dict]) -> dict[str, list[str]]:
    """提取本轮各副作用工具成功产物的 id（下载/知识/题目），供失败轮清理。

    工具在结果里带机读标记：save_download→〔下载ID:x〕、save_to_knowledge→〔知识ID:x〕、
    add_questions→〔题目ID:x,y〕。失败步（is_error）不计。"""
    out: dict[str, list[str]] = {"download": [], "knowledge": [], "questions": []}
    for s in steps or []:
        if s.get("is_error"):
            continue
        r = s.get("result") or ""
        tool = s.get("tool")
        if tool == "save_download":
            out["download"] += _DL_ID_RE.findall(r)
        elif tool == "save_to_knowledge":
            out["knowledge"] += _KB_ID_RE.findall(r)
        elif tool == "add_questions":
            for grp in _Q_ID_RE.findall(r):
                out["questions"] += [x for x in grp.split(",") if x]
    return out


def emit_gate_span(tracer, vt: dict, t0_ns: int) -> None:
    """把交付门判定补发成一个 answer_gate span（每次尝试一个 verify.attempt event）。

    循环跑完后据 t0_ns 显式补发，而非用 start_as_current_span 包住循环：产出这些判定的是个
    async generator，其中的 yield 会把 span 的 contextvar 泄漏进消费者上下文。
    未装 OTel provider 时 tracer 为 no-op，全是空操作。
    """
    sp = tracer.start_span("answer_gate", start_time=t0_ns)
    try:
        sp.set_attribute("app.gate.attempts", vt["attempts"])
        sp.set_attribute("app.gate.retries", vt["retries"])
        sp.set_attribute("app.gate.ok", vt["ok"])
        sp.set_attribute("app.gate.degraded", vt["degraded"])
        if vt.get("gate_error"):   # 校验器故障 → 本轮的「通过」不代表真校验过，须显形
            sp.set_attribute("app.gate.error", vt["gate_error"])
        for h in vt["history"]:
            sp.add_event("verify.attempt", {
                "attempt": h["attempt"], "run_id": h["run_id"], "ok": h["ok"],
                "failed": h["failed"], "hard_failed": h["hard_failed"],
                "critique": h["critique"][:200]})
        if vt["degraded"]:      # 用尽重答次数仍未过 → 标红，便于在追踪后端筛出来
            last = vt["history"][-1]["summary"] if vt["history"] else "未知"
            sp.set_status(Status(StatusCode.ERROR, f"交付门未通过：{last}"))
    finally:
        sp.end()


def _plan_text(progress: list[dict]) -> str:
    """从进度事件里取最后一次任务拆分（scope=plan 的 JSON 文本），供轨迹 judge 回看。"""
    plans = [p["text"] for p in progress if p.get("scope") == "plan"]
    return plans[-1] if plans else ""


def make_chat_router(harness, store, config, question_store=None, wrong_store=None,
                     verifier=None, attachment_store=None, run_manager=None,
                     knowledge_service=None, quiz_service=None,
                     profile_store=None, trajectory_judge=None,
                     exam_session_store=None, url_block_store=None) -> APIRouter:
    router = APIRouter()
    # 简答题判分用 judge completer（考试判分中间件用；客观题不需要模型）
    from ..completion import build_judge_completer
    _exam_judge = build_judge_completer(harness.client, config)
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
    # 交付门插桩：未装 OTel provider 时 get_tracer 返回 no-op tracer，零开销
    _tracer = get_tracer("app.chat")

    def _build_registry(user_id: str, conv_id: str, has_attachments: bool,
                        exam_active: bool = False) -> tuple[ToolRegistry, SourceSink]:
        reg = ToolRegistry()
        sink = SourceSink()
        # 两层包装，顺序有讲究：guard 在内、记源在外。guard 命中登记时抛 ToolError，
        # 该异常先于记源逻辑抛出，故被跳过的网址不会被记成「参考来源」。
        def _reg(t):
            reg.register(wrap_tool(guard_fetch_tool(t, url_block_store), sink))
        for t in harness.registry.tools():
            _reg(t)
        # 用用户级 save_download 覆盖全局那个（同名），使下载文件按用户隔离
        dstore = getattr(harness, "download_store", None)
        if dstore is not None:
            _reg(SaveDownloadTool(
                dstore, config.download_max_mb * 1024 * 1024, user_id))
        # 知识库保存：按用户隔离，写入 knowledge:{user_id} 并建立文档记录，
        # 使内容出现在「知识库」菜单（区别于 remember 写入的私有记忆）。
        if knowledge_service is not None:
            _reg(SaveToKnowledgeTool(knowledge_service, user_id))
        if question_store is not None:
            _reg(SampleQuestionsTool(question_store, user_id))
            _reg(AddQuestionsTool(question_store, user_id))
            _reg(ListQuestionsTool(question_store, user_id))
            _reg(DeleteQuestionsTool(question_store, user_id))
            if quiz_service is not None:
                _reg(GenerateQuestionsTool(quiz_service, user_id))
            # 考试激活时不暴露 save_wrong_answer：判分与保存已由服务端确定性完成，防重复入库
            if wrong_store is not None and not exam_active:
                _reg(SaveWrongAnswerTool(question_store, wrong_store, user_id))
        if wrong_store is not None:
            _reg(SampleWrongAnswersTool(wrong_store, user_id))
            _reg(DeleteWrongAnswersTool(wrong_store, user_id))
        # 服务端托管考试：模型用 start_exam 开考（题源题库/错题集/即席），开考后判分与保存全自动
        if exam_session_store is not None and (question_store is not None or wrong_store is not None):
            _reg(StartExamTool(exam_session_store, user_id, conv_id,
                               question_store=question_store, wrong_store=wrong_store))
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
        # 考试判分中间件：若本会话有 active 考试，把本条消息当作对当前题的作答，服务端判分、
        # 答错确定性存错题集、推进游标，产出注入模型的判定提示。必须先于 _build_registry，
        # 使 exam_active 反映本轮判分后的状态（可能刚好答完/被结束）。
        exam_note, exam_active = "", False
        if exam_session_store is not None:
            exam_note, exam_active = await grade_exam_turn(
                exam_session_store, wrong_store, _exam_judge,
                user_id=user_id, conv_id=req.conversation_id,
                message=req.message)
        registry, source_sink = _build_registry(user_id, req.conversation_id,
                                                 has_attachments, exam_active)
        # 交付门开启需三者皆备：装配了 verifier + 服务端总开关 + 本轮用户开关（默认开，可手动关）
        gate_on = verifier is not None and config.enable_answer_gate and req.verify
        # 喂给模型的消息：带附件时追加只含文件名的名单提示（不含内容），入库仍用原文
        model_message = req.message
        if attachment_metas:
            names = "、".join(m["filename"] for m in attachment_metas)
            model_message = (
                f"{req.message}\n\n[本轮附件] 用户上传了 {len(attachment_metas)} 个文件："
                f"{names}（内容未加载，需要时用 list_attachments/read_attachment 获取，"
                f"或在沙箱 /workspace/uploads/ 下访问）")
        # 考试判定提示（系统权威）：让模型据此讲解并呈现下一题；判分/保存已在服务端完成
        if exam_note:
            model_message = model_message + exam_note

        # 分层上下文：窗口/摘要/检索的重活在此按会话级预算一次，_new_loop 内只做同步拼装。
        _ctx_t0 = time.time()
        # 用户个性化：非空时注入 <user_profile> 块（聊天与考试讲评同源生效）；空则零变更
        profile_block = ""
        if profile_store is not None:
            profile_block = render_profile_block(profile_store.get(user_id))
        base_ctx = await _assembler.build_manager(
            harness.system_prompt + profile_block + EXAM_GUIDE + ATTACHMENT_GUIDE
            + SOURCE_GUIDE + _today_guide(),
            history, req.message, req.conversation_id)
        log.info("上下文组装 conv=%s 历史%d条 耗时%dms",
                 req.conversation_id, len(history), round((time.time() - _ctx_t0) * 1000))

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
            tool_t0: dict[str, float] = {}   # tool_call_id -> 开始时刻，用于算单个工具耗时

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
                        tool_t0[tc.id] = time.time()
                        log.info("工具调用 %s", tc.name)
                    elif isinstance(ev, ToolFinished):
                        st = step_by_id.get(ev.result.tool_call_id)
                        if st is not None:
                            st["result"] = ev.result.content
                            st["is_error"] = ev.result.is_error
                            _t0 = tool_t0.pop(ev.result.tool_call_id, None)
                            _dur = round((time.time() - _t0) * 1000) if _t0 else -1
                            log.info("工具完成 %s 耗时%dms error=%s 输出%d字",
                                     st["tool"], _dur, ev.result.is_error,
                                     len(ev.result.content or ""))
                            if st["tool"] in ("search_memory", "run_python", "run_node", "run_java"):
                                collect["grounding"].append(
                                    {"tool": st["tool"], "content": ev.result.content,
                                     "is_error": ev.result.is_error})
                    elif isinstance(ev, ReasoningDelta):
                        # 累积思考过程供落库，刷新后仍能还原（前端仍实时收到该事件流式展示）
                        collect["reasoning"] = collect.get("reasoning", "") + ev.text
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
            # 关联 id：本轮（后台任务）内每条日志都带 conv/run，便于把一次请求串起来看
            set_log_context(conv_id=req.conversation_id, run_id=turn_run_id)
            # 思考模式（按请求）：透传 enable_thinking 给 LLM 客户端；仅作用于本轮任务的模型调用
            set_extra_body_override({"enable_thinking": req.think})
            log.info("聊天开始 msg=%d字 附件=%d 交付门=%s 思考=%s",
                     len(req.message or ""), len(attachment_metas), gate_on, req.think)
            question = req.message
            delivered = None
            delivered_sources: list[dict] = []   # 交付那次尝试的权威来源
            errored = False
            # 交付门结构化判定轨迹（门未开则保持 None，不落库）：progress 列只存渲染用中文，
            # 统计「哪层失败率高/平均重答几次」要的是这里未拍扁的 failed[]/hard_failed[]。
            verify_trace: dict | None = None
            parts: list[str] = []        # 累积客户端面 TextDelta，供 stop/重启保留已生成部分
            first_token_at: list[float] = []   # 首个 TextDelta 时刻（记一次），用于算首字延迟

            def _acc(ev):
                if isinstance(ev, TextDelta):
                    if not first_token_at:
                        first_token_at.append(time.time())
                        log.info("首字延迟 %dms", round((first_token_at[0] - turn_start) * 1000))
                    parts.append(ev.text)
                    if len(parts) % 25 == 0:   # 去抖 flush：仅为服务重启后能看到断点前部分
                        store.flush_partial(req.conversation_id, turn_run_id, "".join(parts))
                return ev

            def _emit_verify(text, status=None, key=None):
                # progress 存的是渲染用的中文文案；结构化判定（哪层没过/重答几次）走
                # verify_trace → conversation_messages.verify 列，供 stats._gate_stats 统计。
                ev = Progress("verify", text, status=status, key=key)
                progress.append({"scope": ev.scope, "text": ev.text,
                                 "status": ev.status, "key": ev.key, "agent": ev.agent})
                return ev

            def _emit_quality(tscore):
                # 轨迹 judge 三层质量分 → 前端常驻徽章（scope=quality，text 为 JSON）
                payload = json.dumps(
                    {"plan": tscore.plan, "steps": tscore.steps,
                     "final": tscore.final, "feedback": tscore.feedback},
                    ensure_ascii=False)
                st = ("error" if (tscore.final is not None
                                  and tscore.final < config.trajectory_pass_score) else "ok")
                ev = Progress("quality", payload, status=st, key="quality")
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
                               "grounding": [], "progress": progress, "usage": None,
                               "reasoning": ""}
                    run_id_a = uuid4().hex
                    store.add_run(req.conversation_id, run_id_a)
                    source_sink.reset()
                    async for s in _drain(_new_loop(run_id_a), run_id_a, model_message, True, collect):
                        yield _acc(s)
                    errored = collect["final"] is None
                    if not errored:
                        delivered = collect["final"]
                    elif collect["error"] is not None:
                        # loop 抛错：保留已流式输出的部分（若有），把干净提示拼在其后——
                        # 不丢用户已看到的内容，也不泄漏原始错误文案（见
                        # test_chat_run_error_persists_clean_message）。
                        partial = "".join(parts).strip()
                        hint = "（本轮未能完成，请重试）"
                        delivered = f"{partial}\n\n{hint}" if partial else hint
                    else:
                        # 空产出（无文本/无工具调用/未抛错）：loop 只静默 yield 一个空 RunFinished，
                        # 在途客户端收不到任何可见信号 → 前端误显示"…"+"已完成"。此处的错误文案是
                        # 我们自拟的安全提示，故落库同一文案（前端在途会把 RunError 以 "[出错] …"
                        # 追加进气泡），使刷新后与在途所见一致，而非退化成泛化的「本轮未完成」。
                        empty_text = "模型未返回任何内容（可能触发内容策略或上游限流），请重试"
                        delivered = f"[出错] {empty_text}"
                        yield RunError(error=empty_text)
                    delivered_sources = source_sink.snapshot()
                else:
                    # 交付门：缓冲 → 校验 → 不过则回灌重答，最多 answer_gate_max_retries 次
                    corrective = None
                    max_attempts = config.answer_gate_max_retries + 1
                    verify_trace = {"attempts": 0, "retries": 0, "ok": False,
                                    "degraded": False, "gate_error": None, "history": []}
                    gate_t0 = time.time_ns()     # span 起点：循环跑完后据此补发（见 _emit_gate_span）
                    # 未通过轮生成的副作用产物（下载/知识/题目），交付/降级时清理掉
                    stale_fx: dict[str, list[str]] = {"download": [], "knowledge": [], "questions": []}

                    def _purge_side_effects(fx):
                        _dl = getattr(harness, "download_store", None)
                        for _did in fx["download"]:
                            try:
                                if _dl is not None:
                                    _dl.delete(user_id, _did)
                            except Exception:   # 清理失败不阻断交付
                                pass
                        for _kid in fx["knowledge"]:
                            try:
                                if knowledge_service is not None:
                                    knowledge_service.delete(user_id, _kid)
                            except Exception:
                                pass
                        if fx["questions"] and question_store is not None:
                            try:
                                question_store.delete_many(user_id, fx["questions"])
                            except Exception:
                                pass

                    for attempt in range(max_attempts):
                        msg = model_message if corrective is None else corrective
                        collect = {"final": None, "error": None, "steps": [],
                                   "grounding": [], "progress": progress, "usage": None,
                                   "reasoning": ""}
                        run_id_a = uuid4().hex
                        store.add_run(req.conversation_id, run_id_a)
                        source_sink.reset()   # 每次尝试重置，交付时快照该次来源
                        async for s in _drain(_new_loop(run_id_a), run_id_a, msg, False, collect):
                            yield s
                        steps.extend(collect["steps"])
                        draft = (collect["final"] or "").strip()
                        cur_fx = _side_effect_ids(collect["steps"])   # 本轮生成的副作用产物

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
                                    question, draft, collect["grounding"], registry,
                                    steps=collect["steps"])
                            except Exception as e:   # noqa: BLE001
                                # 校验器自身故障（非回答质量问题）→ fail-open：跳过校验照常交付。
                                # 交付门是质量增强，它坏了不该连累用户丢掉一份好答案；且门本就
                                # 默认关闭，「无门」是受支持的状态。但绝不能无声无息：打日志 +
                                # 记进 verify 列的 gate_error，stats 能统计到「多少轮没真校验过」。
                                # 与轨迹 judge 的既有行为一致（verify.py 亦是失败即跳过）。
                                log.warning("交付门校验器故障，本轮跳过校验直接交付：%s", e,
                                            exc_info=True)
                                gate_error = f"{type(e).__name__}: {e}"[:200]
                                verify_trace["gate_error"] = gate_error
                                verdict = Verdict(ok=True)
                            finally:
                                reset_sandbox_conv(stoken)

                        # 轨迹 judge：仅当其它校验项已通过时才花一次独立模型调用，
                        # 回看整轨迹（拆分/关键步/最终）分层打分；final 偏低则并入软门重答。
                        # 按复杂度自动开启：仅当本轮工具步骤 > 1（多步任务）才跑——单步/无工具的
                        # 简单任务无「拆分/多步」可评，跳过省 token 与延迟（前提仍是 config 开了开关）。
                        if (verdict.ok and trajectory_judge is not None
                                and config.enable_trajectory_judge and draft
                                and len(collect["steps"]) > 1):
                            tscore = await trajectory_judge.score(
                                question, _plan_text(progress),
                                _tool_exec_summary(collect["steps"]), draft)
                            yield _emit_quality(tscore)
                            if (tscore.final is not None
                                    and tscore.final < config.trajectory_pass_score):
                                verdict = Verdict(
                                    ok=False, failed=["trajectory"],
                                    critique=tscore.feedback or f"整体质量 {tscore.final} 偏低",
                                    summary="trajectory")

                        # 结构化记这次判定（须在轨迹 judge 可能改写 verdict 之后）。run_id 是与
                        # harness 库 trajectory_events 的接缝：据此可捞出该次尝试（含被否草稿）
                        # 的逐字原始输出——此前每次重答虽各有 run_id，却无处得知它是第几次、被谁否的。
                        verify_trace["history"].append(
                            {"attempt": attempt + 1, "run_id": run_id_a, "ok": verdict.ok,
                             "failed": list(verdict.failed),
                             "hard_failed": list(verdict.hard_failed),
                             "summary": verdict.summary, "critique": verdict.critique})
                        verify_trace["attempts"] = attempt + 1
                        verify_trace["retries"] = attempt      # 重答次数 = 尝试数 - 1
                        verify_trace["ok"] = verdict.ok

                        if verdict.ok:
                            yield _emit_verify("校验通过", status="ok", key=ekey)
                            delivered = draft
                            delivered_sources = source_sink.snapshot()
                            _purge_side_effects(stale_fx)   # 交付本轮，删掉之前失败轮的副作用产物
                            break
                        # error 事件的 text 携带完整原因（critique），供前端展开显示
                        # error 事件 text：中文层名 + 完整原因，供前端展示「哪层没过 + 为什么」
                        layers = failed_layers_zh(verdict.failed) or "校验"
                        reason = (f"{layers}未通过"
                                  + (f"：{verdict.critique}" if verdict.critique else ""))
                        yield _emit_verify(reason, status="error", key=ekey)
                        if attempt == max_attempts - 1:      # 用尽次数 → 降级交付
                            verify_trace["degraded"] = True
                            delivered = draft or "（本轮未完成）"
                            delivered_sources = source_sink.snapshot()
                            errored = not draft
                            if verdict.summary:
                                delivered = (f"⚠️ 此回答未通过自动校验（{verdict.summary}），"
                                             f"请谨慎参考。\n\n" + delivered)
                            _purge_side_effects(stale_fx)   # 降级交付本轮，清理之前失败轮的副作用产物
                            break
                        for _k in stale_fx:   # 本轮未通过、将重答，其副作用产物作废待清理
                            stale_fx[_k] += cur_fx[_k]
                        yield _emit_verify("重答中…", status="running")
                        corrective = (f"你上一版回答未通过自动校验。问题：{verdict.critique}。"
                                      f"请针对性修正后，重新完整回答原问题：{question}")

                    emit_gate_span(_tracer, verify_trace, gate_t0)
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
                _elapsed = round((time.time() - turn_start) * 1000)
                store.finish_turn(req.conversation_id, turn_run_id, final_content,
                                  steps=steps or None, progress=progress or None,
                                  status=status, sources=delivered_sources or None,
                                  tokens=_usage.get("tokens"), cost=_usage.get("cost"),
                                  elapsed_ms=_elapsed, reasoning=collect.get("reasoning") or None,
                                  verify=verify_trace)
                log.info("聊天完成 status=%s 耗时%dms tokens=%s 工具%d次 来源%d条 重答%s次",
                         status, _elapsed, _usage.get("tokens"), len(steps),
                         len(delivered_sources),
                         verify_trace["retries"] if verify_trace else "-")
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
                    elapsed_ms=round((time.time() - turn_start) * 1000),
                    verify=verify_trace)   # 中断时保留已发生的重答记录
                raise
            except Exception as e:  # noqa: BLE001  意外异常也要把占位落成 error，别永远 streaming
                store.finish_turn(
                    req.conversation_id, turn_run_id,
                    delivered or "".join(parts) or "（本轮未能完成，请重试）",
                    steps=steps or None, progress=progress or None, status="error",
                    elapsed_ms=round((time.time() - turn_start) * 1000),
                    verify=verify_trace)   # 出错时保留已发生的重答记录
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

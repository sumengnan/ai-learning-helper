# app/api/chat.py
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from harness.approval import reset_context, resolve, set_context
from harness.events import (
    ModelUsage, Progress, ReasoningDelta, RunError, RunFinished, TextDelta,
    ToolFinished, ToolStarted)
from harness.llm.openai_compat import (
    json_output, reset_extra_body_override, set_extra_body_override)
from harness.loop.agent_loop import AgentLoop
from harness.persistence.serialize import event_to_dict
from harness.progress import reset_emitter, set_emitter
from opentelemetry.trace import Status, StatusCode

from harness.reliability.budget import BudgetTracker
from harness.telemetry.tracer import get_tracer
from harness.tools.base import ToolRegistry
from harness.tools.builtins.memory_search import SearchKnowledgeTool, SearchMemoryTool
from harness.tools.builtins.memory_write import RememberTool
from harness.types import Message, Role
from harness.usage import reset_pricing, set_pricing

from ..auth import current_user
from ..completion import build_fast_completer
from ..context_assembly import ContextAssembler
from ..conversation_memory import ConversationMemoryService
from ..orchestration.orchestrator import VERIFY_TRACE_KEY
from ..orchestration.executor import CLARIFY_GUIDE
from ..profile import render_profile_block
from ..side_effects import SideEffectPurger
from ..sandbox_manager import reset_sandbox_conv, sandbox_guide, set_sandbox_conv
from ..summaries import SummaryStore
from ..summarizer import RollingSummarizer
from ..sources import SOURCE_GUIDE, SourceSink, wrap_tool
from ..url_blocklist import guard_fetch_tool
from ..tools.attachment_tools import ListAttachmentsTool, ReadAttachmentTool
from ..tools.plan_tool import reset_plan_clock, set_plan_clock
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
from ..tools.plan_tool import (
    FINALIZE_SYSTEM, finalize_user_prompt, merge_finalized, unfinished_steps)
from ..today import today_guide
from ..tools.validating import ValidatingTool, relevance_check
from ..tools.save_download import SaveDownloadTool
from ..quiz_service import _strip_fence
from ..logging_setup import set_log_context
from ..verify import Verdict, _tool_exec_summary, failed_layers_zh

log = logging.getLogger("app.chat")

# 交付门缓冲后补发终稿时，把文本切成小片以保留打字机效果
_DELIVER_CHUNK = 40

# 「本轮开了校验门」信号的固定 key（前端 VerifyBadge.tsx 有同名常量，改这里必须同时改那里）。
# 借 scope=verify 通道下发，但它不是校验进展，前端不得把它渲染成校验徽章。详见发出处的注释。
GATE_OPEN_KEY = "verify:gate-open"


def _chunks(text: str, size: int = _DELIVER_CHUNK):
    for i in range(0, len(text), size):
        yield text[i:i + size]

EXAM_GUIDE = (
    "\n\n你具备「题库 / 错题集 / 模拟考试」能力：\n"
    "- 【正式模拟考试优先用 start_exam 开考】：从题库随机抽题(source=bank)/错题集抽题(source=wrong)，"
    "或即席出题(source=adhoc，需在 questions 传入含答案的题)。开考后每题的判分与「答错自动存错题集」"
    "都由系统在后台确定性完成——你【无需也不要】调用 save_wrong_answer，只需把系统返回的题目呈现给用户，"
    "并在系统给出「[考试系统判定]…」提示后据其讲解、再呈现系统给的下一题。\n"
    "- 【用户指名要考某几道题时必须用 source=ids】：如「刚才生成的那 5 道题，考试」「就考这几题」，"
    "把那批题的 id 传给 start_exam(source=\"ids\", question_ids=[...])，按传入顺序出题。"
    "题目 id 来自 add_questions/generate_questions 返回末尾的〔题目ID:...〕标记（即那批新题，按出题顺序），"
    "也可用 list_questions 查。此时【绝不能用 source=bank 顶替】——那是全库随机抽，"
    "考出来的不是用户要的那几道，属于明确的错误。\n"
    "- 【题目 id 绝不出现在给用户的回答里】：id 是系统内部凭证（形如 3f2a…），对用户毫无意义。"
    "它只用于填工具参数。需要在回答里提到某道题时，一律用题干（可截短），例如"
    "「第 3 天：完成『什么是自注意力机制』等 3 道练习」，而不是罗列一串 id。"
    "制定学习计划、总结、复述题目清单时尤其注意这一点。\n"
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

# EXAM_GUIDE 是常驻提示里最大的一块（约 1600 字，占常驻 60%），但绝大多数轮次用不上
# （问答/写代码/查资料）。改为命中考试语境才注入 —— 但绝不能只看当前这句话：
# 「即时练习」是模型自驱的多轮流程（sample_questions 抽题后逐题问答），中间轮用户只回
# 「A」，既无触发词也无服务端 session，此时若丢了指引，「答错必存」等保证会静默失效。
# 故用三个信号任一即注入，且刻意偏向注入 —— 漏注入=破坏保证（高代价），多注入=浪费 0.3%
# 窗口（低代价）。不做成 skill 的原因：实测模型从不主动 load_skill（0/440 次调用）。
_EXAM_TRIGGER = re.compile(
    "考试|考我|考考|考核|模拟考|测验|测测|测一下|测下|小测|刷题|练题|做题|答题|做题|"
    "出题|命题|练习|题库|错题|抽题|几道题|来道题|来几题|做几道|做一道|背题|默写|开考")
_EXAM_TOOLS = frozenset({
    "start_exam", "sample_questions", "sample_wrong_answers", "save_wrong_answer",
    "list_questions", "add_questions", "generate_questions",
    "delete_questions", "delete_wrong_answers"})
_EXAM_HISTORY_WINDOW = 16   # 覆盖一次批量抽题后逐题问答的往返（每题约 2 条消息）


def _needs_exam_guide(message: str, history, exam_active: bool) -> bool:
    """本轮是否处于考试/练习语境，需注入 EXAM_GUIDE。任一信号命中即注入：
    1) 本条消息含考试/刷题/错题等意图词；
    2) 有 active start_exam 会话（考试进行中，用户此刻多半只回「A」）；
    3) 近 _EXAM_HISTORY_WINDOW 条历史里出现过考试类工具调用，或用户说过意图词
       —— 覆盖模型自驱的多轮练习（中间轮无触发词、无 session）。
    """
    if exam_active:
        return True
    if _EXAM_TRIGGER.search(message or ""):
        return True
    for m in (history or [])[-_EXAM_HISTORY_WINDOW:]:
        if any(tc.name in _EXAM_TOOLS for tc in (m.tool_calls or [])):
            return True
        if m.role == Role.USER and isinstance(m.content, str) \
                and _EXAM_TRIGGER.search(m.content):
            return True
    return False


def _in_stateful_exam(history, exam_active: bool) -> bool:
    """是否真的**身处**有状态的考试流程中（考试会话进行中，或模型自驱的多轮练习途中）。

    与 _needs_exam_guide 的分工：那个判「要不要注入考试指引」，刻意偏向命中——漏注入
    会让「答错必存」等保证静默失效（高代价），多注入只费约 0.3% 窗口（低代价），故纯
    触发词也算数。本函数用于代价高得多的判断（是否关掉技能路由），所以只认两个**真有
    状态**的信号：进行中的考试会话、近期确实调过考试工具。单凭本条消息提到「错题/刷题」
    不算——那多半是在提要求，而不是正在答题。

    覆盖 Bug：「讲讲我的错题」命中考试触发词「错题」→ 技能路由被整段跳过，而
    wrong-answer-remediation 技能的触发词恰恰就是「错题/我的错题/讲讲错题」这些词——
    技能被自己的核心触发词挡在门外，实测只有「我哪里薄弱」这类不含考试词的说法能命中。
    """
    if exam_active:
        return True
    return any(tc.name in _EXAM_TOOLS
               for m in (history or [])[-_EXAM_HISTORY_WINDOW:]
               for tc in (m.tool_calls or []))


ATTACHMENT_GUIDE = (
    "\n\n用户可能在消息中上传附件（文件内容默认不在上下文里，需要时再取）：\n"
    "- 用 list_attachments 查看本对话的附件清单（id/文件名/类型）。\n"
    "- 用 read_attachment(attachment_id) 读取具体内容：txt/pdf/word 返回文本，图片作为视觉加载。\n"
    "- 所有附件也已放入沙箱 /workspace/uploads/，可用 run_python/run_shell 直接读取或执行。\n"
    "- 只在确有需要时才读取附件，不要无谓地逐个打开。\n")

# CLARIFY_GUIDE（信息不足先问、不要猜）现集中定义在 orchestration.executor，供主聊天与编排器共用。
# 北京时间（东八区）：本应用面向中文用户，用它作为「今天」的基准
_CN_TZ = timezone(timedelta(hours=8))


def _today_guide() -> str:
    """每请求注入当前日期，避免模型沿用训练数据里的年份做时间推算（如把「未来3年」从旧年份起算）。

    实现已收敛到 app/today.py：同一个「今天」原先在这里按北京时间、在 executor 里按服务器
    本地时区各写一遍，跨时区部署会差一天且不报错。此处保留薄封装，仅为不破坏既有调用与测试。
    """
    return today_guide()


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


def _plan_from_orchestrator(progress: list[dict]) -> bool:
    """本轮最后一条 plan 进度是不是编排器发的（其步骤带 id）。

    简单直答路径里模型会自己调 update_plan 发一份 ReAct 清单，同样是 scope="plan"，
    但只有 title/status。两者必须区分：清单收尾 shim 是给「模型自述、可能忘了更新」的
    清单用的，编排器的计划由状态机保证每步有终态，无需也不该补。
    """
    plans = [p for p in progress if p.get("scope") == "plan"]
    if not plans:
        return False
    try:
        steps = json.loads(plans[-1].get("text") or "[]")
    except (ValueError, TypeError):
        return False
    return isinstance(steps, list) and any(isinstance(s, dict) and s.get("id") for s in steps)


def _side_effect_ids(steps: list[dict]) -> dict[str, list[str]]:
    """提取本轮各副作用工具成功产物的 id（下载/知识/题目），供失败轮清理。

    工具在结果里带机读标记：save_download→〔下载ID:x〕、save_to_knowledge→〔知识ID:x〕、
    add_questions/generate_questions→〔题目ID:x,y〕。失败步（is_error）不计。"""
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
        elif tool in ("add_questions", "generate_questions"):
            for grp in _Q_ID_RE.findall(r):
                out["questions"] += [x for x in grp.split(",") if x]
    return out


# 副作用工具 → 产物的说法（供重答提示点名，让模型知道要重做什么）
_FX_KIND = {
    "save_download": "文件",
    "save_to_knowledge": "知识库条目",
    "add_questions": "题库题目",
    "generate_questions": "题库题目",
}


def _redo_fx_note(steps: list[dict]) -> str:
    """重答提示的附注：点名上一版产生的副作用产物，要求重新调用工具再存一次。

    每次 attempt 都是全新 RunState，context 只含系统提示 + 会话历史 + 纠正指令——模型
    看不到上一版自己调过哪些工具。而上一版存下的文件/知识/题目会在交付时被
    _purge_side_effects 删掉（它们属于未通过的那版）。两件事一叠加：模型不知道该重存、
    旧产物又被删，用户最终一个文件都拿不到。故必须在纠正指令里明说。
    """
    made: list[str] = []
    for s in steps or []:
        if s.get("is_error"):
            continue
        kind = _FX_KIND.get(s.get("tool") or "")
        if not kind:
            continue
        args = s.get("args") if isinstance(s.get("args"), dict) else {}
        name = str(args.get("filename") or "").strip()
        made.append(f"{s['tool']}（{kind}{f'《{name}》' if name else ''}）")
    if not made:
        return ""
    return ("\n注意：你上一版曾调用 " + "、".join(dict.fromkeys(made))
            + "，这些产物已随未通过的那一版一并作废删除。本次回答若仍应产出它们，"
              "【必须重新调用相应工具再存一次】——上一版存过不算数，不重新调用就真的"
              "什么都没留下，用户会以为文件已保存却找不到。")


def _drop_purged_marks(steps: list[dict], fx: dict[str, list[str]]) -> None:
    """把已清理产物的机读标记从步骤结果里抹掉，并注明作废原因。

    前端据 save_download 结果里的〔下载ID:x〕渲染下载按钮，而 steps 累积了本轮所有尝试
    （含被否那些）。产物删了标记还留着 → 用户会看到一个指向已删文件的死按钮；重答后
    尤其明显：新旧两版的按钮并排列出，点旧的必然 404。
    """
    marks = [f"〔下载ID:{i}〕" for i in fx["download"]]
    marks += [f"〔知识ID:{i}〕" for i in fx["knowledge"]]
    for s in steps or []:
        r = s.get("result") or ""
        hit = [m for m in marks if m in r]
        if not hit:
            continue
        for m in hit:
            r = r.replace(m, "")
        s["result"] = r.rstrip() + "\n（该版本未通过校验，此产物已作废删除）"


def _split_stale_fx(stale: dict[str, list[str]], cur: dict[str, list[str]]
                    ) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """把未通过轮的产物按类分成「该删的」与「该留的」。

    删：交付的那一版自己产出了同类产物 → 旧的已被它取代，留着就是指向废内容的死按钮。
    留：交付的那一版一件同类产物都没有 → 这是唯一的一份，删了用户就彻底空手。

    后者是 _redo_fx_note 的确定性兜底：那条纠正指令只是「告诉」模型重做，管不住它照不照
    做；真没照做时，宁可留下上一版的产物（并在步骤里标明出处），也不能让用户什么都拿不到。

    铁律：**交付版正在用的 id 一个都不能删**。产物按内容判重（见 DownloadStore.create），
    重答时若文件内容与上一版逐字节相同，重存拿回的就是同一条记录——此时 stale 与 cur 里是
    同一个 id，照「cur 非空就把 stale 全删」的老写法会把交付版自己的文件删掉：校验通过了、
    答案交付了，用户却既没有下载按钮，点在途界面的旧按钮还是 404。而「校验挂在正文措辞、
    文件内容原样重生成」正是最常见的重答形态，故这不是边角情况。
    """
    purge = {k: ([i for i in v if i not in set(cur.get(k) or ())] if cur.get(k) else [])
             for k, v in stale.items()}
    keep = {k: ([] if cur.get(k) else v) for k, v in stale.items()}
    return purge, keep


def _mark_carried_over(steps: list[dict], fx: dict[str, list[str]]) -> None:
    """给保留下来的上一版产物在步骤结果里标明出处（机读标记留着，下载按钮仍可用）。

    产物出自未通过校验的那一版，而最终答案是另一版写的——不说清楚，用户会默认二者一致。
    """
    marks = [f"〔下载ID:{i}〕" for i in fx["download"]]
    marks += [f"〔知识ID:{i}〕" for i in fx["knowledge"]]
    if not marks:
        return
    for s in steps or []:
        r = s.get("result") or ""
        if not any(m in r for m in marks):
            continue
        s["result"] = (r.rstrip()
                       + "\n（此产物由未通过校验的那一版生成；最终回答未重新生成它，"
                         "已为你保留，请对照最终回答确认后再用）")


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


def _is_retrieval_tool(name: str) -> bool:
    """该工具是否为「联网检索/抓取外部内容」——其结果与知识库同属模型作答的检索依据。
    覆盖内置 browse/http_request 与各类 MCP 搜索工具（名字含 search/web，如
    mcp__websearch__bailian_web_search），故用前缀/关键词而非硬编码具体工具名。"""
    n = name or ""
    if n in ("browse", "http_request"):
        return True
    return n.startswith("mcp__") and ("search" in n or "web" in n)


def _msg_text(m) -> str:
    """取消息正文（兼容多模态 content 为 parts 列表的情形）。"""
    c = m.content
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return ""


def _recent_dialogue(history, max_msgs: int = 6, max_chars: int = 2000) -> str:
    """渲染最近几轮 user/assistant 正文，供 judge 解读用户本轮的简短回复。

    只传上一条 assistant 不够：菜单后可能夹着澄清往返，或用户回应的是几轮前的列表。
    故取最近若干条对话（跳过纯工具调用/工具结果消息——噪音大且冗长），从最新往回收，
    到条数或字数上限即止，保证保留最贴近本轮的上下文（列表/菜单通常就在这里）。
    仍有边界：引用远超窗口的内容、或只出现在工具结果里的选项，judge 未必拿得到——
    但那已是少数，且模型本身有完整窗口，judge 这里给足最近对话即可覆盖绝大多数。
    """
    picked: list[str] = []
    used = 0
    for m in reversed(history or []):
        role = getattr(m.role, "value", m.role)
        if role not in ("user", "assistant"):
            continue
        text = _msg_text(m).strip()
        if not text:                      # 纯工具调用的 assistant 消息无正文 → 跳过
            continue
        line = f"{'用户' if role == 'user' else 'AI'}：{text}"
        if picked and used + len(line) > max_chars:
            break
        picked.append(line)
        used += len(line)
        if len(picked) >= max_msgs:
            break
    picked.reverse()                      # 收集是倒序，输出按时间正序
    return "\n".join(picked)


def _plan_text(progress: list[dict]) -> str:
    """从进度事件里取最后一次任务拆分（scope=plan 的 JSON 文本），供轨迹 judge 回看。"""
    plans = [p["text"] for p in progress if p.get("scope") == "plan"]
    return plans[-1] if plans else ""


async def _finalize_stale_plan(complete, progress: list[dict], steps: list[dict],
                               trace: dict) -> Progress | None:
    """交付前收尾任务清单：清单还留着 pending/running 就补一次定向调用，返回要发的事件。

    为什么在这里、为什么不接进交付门：
    - 检测是纯确定性的（unfinished_steps 只解析文本），零成本，故无条件先跑；
    - 但**不能**记进 verdict.failed —— 那会触发整轮重答（重跑检索、重新生成文件），
      为一个纯记账问题付整轮成本。实测那种轮次的答案本身是好的（judge 打 95 分）。
      所以只花一次小调用让模型把清单收尾，答案一个字不动。
    - 也不能由服务端替它标 done：「第3步做没做」工具轨迹里推不出来（save_download 对应
      哪一条是语义匹配）。只能问模型，但问的时机由服务端确定性地掐——这就是「不靠自觉」。
    """
    plan_text = _plan_text(progress)
    left = unfinished_steps(plan_text)
    trace["stale"] = bool(left)
    if not left:
        return None
    trace["unfinished"] = len(left)
    try:
        with json_output():
            raw = await complete(FINALIZE_SYSTEM,
                                 finalize_user_prompt(plan_text, _tool_exec_summary(steps)))
        finalized = json.loads(_strip_fence(raw))
        if isinstance(finalized, dict):   # json_object 信封 {"items":[...]}；漏包时兜底裸数组
            finalized = finalized.get("items")
        merged = merge_finalized(plan_text, finalized)
    except Exception as e:   # noqa: BLE001
        log.warning("清单收尾调用失败，保留原样（前端会如实标『状态未知』）：%s", e)
        trace["finalize_error"] = f"{type(e).__name__}: {e}"[:200]
        return None
    if merged is None:       # 模型改了标题/条数/没给终态 → 驳回，宁可不收尾也不显示错清单
        log.warning("清单收尾结果不可信（条数/标题/状态不合规），保留原样")
        trace["finalize_error"] = "rejected"
        return None
    trace["finalized"] = True
    return Progress(scope="plan", text=json.dumps(merged, ensure_ascii=False), key="plan")


def make_chat_router(harness, store, config, question_store=None, wrong_store=None,
                     verifier=None, attachment_store=None, run_manager=None,
                     knowledge_service=None, quiz_service=None,
                     profile_store=None, trajectory_judge=None,
                     exam_session_store=None, pending_store=None,
                     url_block_store=None, user_store=None) -> APIRouter:
    router = APIRouter()
    # 简答题判分用 judge completer（考试判分中间件用；客观题不需要模型）
    from ..completion import build_judge_completer
    _exam_judge = build_judge_completer(harness.client, config)
    # 清单收尾用 judge completer：活很简单（照工具摘要把 4 条填终态），但每次都要在
    # 交付前串行跑一次，所以用便宜/快的那个端点；未配 judge_model 时自动回退主模型。
    _plan_finalizer = build_judge_completer(harness.client, config)
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
                build_fast_completer(harness.client, config),
                # 计数模型须跟着摘要模型走：它决定 max_summary_tokens 按谁的分词器量
                model=config.fast_model or config.model,
                max_summary_tokens=config.context_summary_max_tokens)
        if getattr(config, "context_enable_retrieval", True) and \
                getattr(harness, "memory", None) is not None:
            _conv_memory = ConversationMemoryService(
                harness.memory,
                writer=getattr(harness, "memory_writer", None),
                sample_rate=config.memory_write_sample_rate)
    _assembler = ContextAssembler(config, config.model,
                                  summarizer=_summarizer, conv_memory=_conv_memory)
    # 记忆整合：在途会话集合，防同一会话并发整合互抢 set_superseded
    _consolidating: set[str] = set()
    # 持有后台任务的强引用，防止 asyncio 在其运行中把 task GC 掉；完成即移除。
    _bg_tasks: set = set()

    def _maybe_consolidate(conv_id: str) -> None:
        """本会话 episodic 攒够了就在后台整合成 semantic。

        【不 await】：gen() 还没返回前，这个 run 在 RunManager 里仍算「在途」，而
        active_run_for_conv 是并发守卫——等整合跑完（每簇一次 LLM）会让用户的下一句
        直接吃 409。故 fire-and-forget，失败只记日志。

        计数按 episodic 而非总数：consolidate 只吃 episodic，用总数会让「一堆 semantic、
        零 episodic」的会话每轮都空转一次聚类。整合后 episodic 被标 superseded、计数
        回落，自然不会每轮重触发。
        """
        _maintainer = getattr(harness, "memory_maintainer", None)
        _mstore = getattr(harness, "memory_store", None)
        _after = getattr(config, "memory_consolidate_after", 0)
        if _maintainer is None or _mstore is None or _after <= 0:
            return
        if conv_id in _consolidating:          # 上一轮的整合还在跑
            return
        try:
            from harness.memory.record import MemType
            n = _mstore.count_by_owner(conv_id, "conversation", mem_type=MemType.EPISODIC)
        except Exception:                      # 计数失败不该影响聊天
            return
        if n < _after:
            return

        async def _run() -> None:
            try:
                r = await _maintainer.maintain(conv_id, "conversation")
                log.info("记忆整合 conv=%s episodic=%d → %s", conv_id, n, r)
            except Exception as e:             # noqa: BLE001
                log.warning("记忆整合失败 conv=%s：%s", conv_id, e, exc_info=True)
            finally:
                _consolidating.discard(conv_id)

        _consolidating.add(conv_id)
        asyncio.create_task(_run())

    def _spawn_post_turn(conv_id: str, seq: int, text: str) -> None:
        """把「L3 智能记忆写入 + 记忆整合」挪到后台执行，不阻塞本轮 SSE 流关闭。

        这两件事与答案无关（RunFinished 早已送出）：此前 gen() 直接 await 记忆写入（提炼+调和，
        最多 2 次 LLM + embedding），会拖着流不关、前端一直转圈、且此 run 仍在 RunManager
        里在途 → 用户连下一句都发不了。改成独立 task 后 gen() 立即返回：状态即刻变已完成、
        并发守卫立即释放。代价：进程正好在写记忆时重启会丢这条在写的记忆（best-effort）。

        整合放在写入完成之后串起来，保持「先写 episodic 再按数量整合」的因果（_maybe_consolidate
        要数本会话 episodic 条数）。"""
        if _conv_memory is None or not text.strip():
            return

        async def _run() -> None:
            try:
                await _conv_memory.record_turn(conv_id, seq, text)
            except Exception as e:   # noqa: BLE001  记忆写入 best-effort，失败不影响聊天
                log.warning("后台记忆写入失败 conv=%s：%s", conv_id, e, exc_info=True)
            _maybe_consolidate(conv_id)

        t = asyncio.create_task(_run())
        _bg_tasks.add(t)
        t.add_done_callback(_bg_tasks.discard)

    # 交付门插桩：未装 OTel provider 时 get_tracer 返回 no-op tracer，零开销
    _tracer = get_tracer("app.chat")

    def _build_registry(user_id: str, conv_id: str, has_attachments: bool,
                        exam_active: bool = False,
                        created_downloads: set | None = None
                        ) -> tuple[ToolRegistry, SourceSink]:
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
                dstore, config.download_max_mb * 1024 * 1024, user_id,
                created_ids=created_downloads))
        # 记忆/知识库检索：都必须按用户覆盖全局那个。assembly 里构造的是无冒号的默认
        # collection（collection_to_scope 判成 owner=_global），而真实数据写在
        # knowledge:{user_id} / memory:{user_id} —— 两个 owner 永不相交，不覆盖的话模型
        # 在聊天里永远搜不到东西，还会连带让交付门的 grounding 校验因「检索恒无命中」而形同虚设。
        # 保持与 assembly.py 同款的包装策略：知识库包每步校验，记忆不包（空记忆是常态）。
        _mem = getattr(harness, "memory", None)
        if _mem is not None:
            _st = SearchKnowledgeTool(_mem, collection=f"knowledge:{user_id}",
                                      default_k=config.search_top_k)
            _reg(ValidatingTool(_st, relevance_check) if config.enable_step_check else _st)
            _reg(SearchMemoryTool(_mem, collection=f"memory:{user_id}",
                                  default_k=config.search_top_k))
            # remember 同样按用户覆盖：否则写入落到 _global/memory，而检索查的是
            # memory:{user_id}，写进去的记忆永远召不回来（拆分前就是这个 bug）。
            _reg(RememberTool(_mem, collection=f"memory:{user_id}"))
        # 知识库保存：按用户隔离，写入 knowledge:{user_id} 并建立文档记录，
        # 使内容出现在「知识库」菜单（区别于 remember 写入的私有记忆）。
        if knowledge_service is not None:
            _reg(SaveToKnowledgeTool(knowledge_service, user_id))
        if question_store is not None:
            _reg(SampleQuestionsTool(question_store, user_id))
            _reg(AddQuestionsTool(question_store, user_id))
            _reg(ListQuestionsTool(question_store, user_id))
            # 传 pending_store：删除改为「登记待确认」，由用户在界面确认后经 API 执行。
            # 执行子步没有与用户对话的通道，工具描述里那句「先取得确认」在此路径上
            # 本就无法满足——只能把确认动作挪到界面上。
            _reg(DeleteQuestionsTool(question_store, user_id, pending_store, conv_id))
            if quiz_service is not None:
                _reg(GenerateQuestionsTool(quiz_service, user_id))
            # 考试激活时不暴露 save_wrong_answer：判分与保存已由服务端确定性完成，防重复入库
            if wrong_store is not None and not exam_active:
                _reg(SaveWrongAnswerTool(question_store, wrong_store, user_id))
        if wrong_store is not None:
            _reg(SampleWrongAnswersTool(wrong_store, user_id))
            _reg(DeleteWrongAnswersTool(wrong_store, user_id, pending_store, conv_id))
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
        # 本轮新建的下载 id：工具往里登记，清理时据此只删本轮自己建的（内容去重会让
        # create() 返回用户早先那条记录的 id，照删就是删掉他上周存的文件）。
        created_downloads: set = set()
        registry, source_sink = _build_registry(user_id, req.conversation_id,
                                                 has_attachments, exam_active,
                                                 created_downloads=created_downloads)
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
        # 姓名与个性化同块注入：让模型知道该怎么称呼用户。取不到（老账号没填姓名、
        # 未注入 user_store）就退回只有个性化，行为与之前一致。
        _full_name = ""
        if user_store is not None:
            _u = user_store.get(user_id)
            _full_name = (_u or {}).get("full_name", "")
        profile_block = ""
        if profile_store is not None or _full_name:
            _profile = profile_store.get(user_id) if profile_store is not None else None
            profile_block = render_profile_block(_profile, full_name=_full_name)
        # 附件指引与附件工具同条件注入：has_attachments 为假时 list_attachments/
        # read_attachment 根本没注册（见 _build_registry），此时还介绍它们的用法，等于
        # 告诉模型一批它没有的工具——比浪费 token 更糟。
        attachment_guide = ATTACHMENT_GUIDE if has_attachments else ""
        # 有沙箱才提醒环境：无沙箱时这些工具根本没注册，介绍其 cwd/镜像/联网只会误导模型
        sandbox_dir_guide = (sandbox_guide(config)
                             if getattr(harness, "sandbox", None) is not None else "")
        # EXAM_GUIDE 命中考试语境才注入（约省 60% 常驻）。exam_active 已由上面的
        # grade_exam_turn 判定；history 用于识别模型自驱的多轮练习。工具本身仍常驻注册，
        # 只省指引文本 —— 万一触发词漏判，模型仍能靠工具描述兜底，是降级而非失能。
        exam_guide = EXAM_GUIDE if _needs_exam_guide(
            req.message, history, exam_active) else ""
        # 考试/练习是有状态、多轮、模型驱动的交互流程（开考→逐题判分交接），只适合 ReAct 单循环：
        # 模型调 start_exam 拿到题、同一轮原样呈现、下一轮由 grade_exam_turn 拦截判分。编排器的
        # plan→execute→synthesize 会把它拆成多步再二次概括，吞掉「原样呈现第一题」，且 Critic 判某步
        # 不合格触发重试会再次 start_exam 把考试重置。故凡注入考试指引（=命中考试语境）即钉死简单直答。
        force_simple = bool(exam_guide)
        # 技能路由的关闭条件比 force_simple 窄一档：force_simple 宽是对的（「考我10道题」
        # 这类开考请求必须走单循环，否则第一题会被多步汇总吞掉），但它顺带把技能路由也
        # 关了——「讲讲我的错题」只因含「错题」二字就被判成考试语境，而错题精讲技能的触发词
        # 正是这些词，技能被自己的触发词挡在门外。技能剧本只在**正在逐题作答**时才真会干扰
        # 推进，故这里只认真有状态的信号。
        in_stateful_exam = _in_stateful_exam(history, exam_active)
        # 清单收尾结果 → 并进 context 列，供统计「模型多久不收一次尾 / 补救成没成」
        plan_trace: dict = {}
        ctx_trace: dict = {}      # 上下文组装结果 → finish_turn 落 context 列，供 stats 统计
        base_ctx = await _assembler.build_manager(
            harness.system_prompt + CLARIFY_GUIDE + profile_block + exam_guide
            + attachment_guide + sandbox_dir_guide + SOURCE_GUIDE + _today_guide(),
            history, req.message, req.conversation_id, trace=ctx_trace)
        log.info("上下文组装 conv=%s 历史%d条 耗时%dms %s",
                 req.conversation_id, len(history), round((time.time() - _ctx_t0) * 1000),
                 ctx_trace)

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
                price_map=config.price_map, tool_result_max_chars=config.tool_result_max_chars,
                loop_detect_window=config.loop_detect_window)

        async def _drain(loop_obj, run_id_a, message, passthrough, collect):
            """跑一次 AgentLoop，逐事件产出 SSE 串；把 final/steps/grounding 收进 collect。

            passthrough=False（交付门）时正文 TextDelta 照常流式给用户（先看后校验），
            仅压住 RunFinished/RunError —— 这两个终态由调用方在校验通过后合成，避免答案还没
            校验就被前端标「已完成」。未过要重答时，调用方先发 scope=reset 清屏、再让新一版流式。
            """
            queue: asyncio.Queue = asyncio.Queue()
            sentinel = object()
            step_by_id: dict[str, dict] = {}
            tool_t0: dict[str, float] = {}   # tool_call_id -> 开始时刻，用于算单个工具耗时

            # 用量事件旁路：带模型名的 ModelUsage（编排器各子调用 record_usage、embedding/rerank
            # 上报）经 emit() 进这个队列，被 _merged 并入主事件流 → 经 sink 落 trajectory（进历史
            # 分模型统计）+ 前端；其余 emit 事件（沙箱进度等）直达 queue（仅前端）。
            usage_q: asyncio.Queue = asyncio.Queue()

            def _emit(ev):
                (usage_q if isinstance(ev, ModelUsage) else queue).put_nowait(ev)

            async def _merged(src):
                async for ev in src:
                    while not usage_q.empty():
                        yield usage_q.get_nowait()
                    yield ev
                while not usage_q.empty():
                    yield usage_q.get_nowait()

            async def pump():
                token = set_emitter(_emit)
                atoken = set_context(run_id=run_id_a, timeout=config.sandbox_approval_timeout)
                stoken = set_sandbox_conv(req.conversation_id)
                ptoken = set_plan_clock()   # 本轮步骤计时表；重答的每次尝试各自重新计时
                # 思考模式（按请求）：只包住主循环——聊天页那个开关的语义是「我这个问题不用
                # 想那么久」，管的是回答用户的那些调用，而不是交付门校验、记忆调和、记忆整合
                # 这些旁路。此前它设在 gen() 里且从不 reset，那些旁路全都悄悄继承了它。
                btoken = set_extra_body_override({"enable_thinking": req.think})
                # 按模型计价上下文：扁平价表 price_map + 默认分层表 + 按模型分层表，主循环与其派生的所有
                # 子任务（编排器 executor/synthesize/planner/critic 走各自模型）都据各自 model 名精确计价。
                cttoken = set_pricing(
                    price_map=config.price_map,
                    tiers=config.model_price_tiers,
                    tiers_by_model=config.model_price_tiers_by_model)
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
                    async for ev in harness.sink.wrap(_merged(loop_obj.run(message))):
                        queue.put_nowait(ev)
                except Exception as e:  # 兜底成 RunError，避免流卡死
                    queue.put_nowait(RunError(error=str(e)))
                finally:
                    reset_pricing(cttoken)
                    reset_extra_body_override(btoken)
                    reset_plan_clock(ptoken)
                    reset_sandbox_conv(stoken)
                    reset_context(atoken)
                    reset_emitter(token)
                    queue.put_nowait(sentinel)

            task = asyncio.create_task(pump())
            # 思考计时：从首个 reasoning token 到其后首个正文 token 的墙钟（monotonic，不受
            # 系统调时影响）。带工具的思考会分多段产出 reasoning，各段累加。仅供思考块顶部显示，
            # 与整轮 elapsed 无关。
            reason_t0: float | None = None
            reason_ms = 0.0
            try:
                while True:
                    ev = await queue.get()
                    if ev is sentinel:
                        break
                    if isinstance(ev, RunFinished):
                        collect["final"] = ev.message.content
                        if reason_t0 is not None:   # 思考到底、无正文（纯推理答复）→ 收尾计时
                            reason_ms += (time.monotonic() - reason_t0) * 1000
                            reason_t0 = None
                        if reason_ms > 0:
                            collect["reasoning_ms"] = int(reason_ms)
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
                            # 只收 search_knowledge：search_memory 查的是 AI 自己记的偏好，
                            # 不是可引用的资料依据，不该参与 grounding 判定。
                            if st["tool"] in ("search_knowledge", "run_python", "run_node", "run_java"):
                                collect["grounding"].append(
                                    {"tool": st["tool"], "content": ev.result.content,
                                     "is_error": ev.result.is_error,
                                     # 联网检索与知识库同为「检索到的依据」；标 retrieval=True 供
                                     # grounding 校验一并纳入——否则联网来的事实会被判「不在知识库」。
                                     "retrieval": st["tool"] == "search_knowledge"})
                            elif st["tool"] in ("read_attachment", "read_file"):
                                # 读入的用户文档/附件正文：整理成笔记/总结时的作答依据，纳入
                                # grounding 核查资料（不标 retrieval，故不单独触发 grounding，
                                # 仅当本轮另有 search_knowledge 命中时作为核查上下文）。
                                collect["grounding"].append(
                                    {"tool": st["tool"], "content": ev.result.content,
                                     "is_error": ev.result.is_error})
                            elif _is_retrieval_tool(st["tool"]):
                                # 联网检索/抓取网页：也是模型据以作答的外部依据，纳入 grounding
                                collect["grounding"].append(
                                    {"tool": st["tool"], "content": ev.result.content,
                                     "is_error": ev.result.is_error, "retrieval": True})
                    elif isinstance(ev, ReasoningDelta):
                        # 累积思考过程供落库，刷新后仍能还原（前端仍实时收到该事件流式展示）
                        collect["reasoning"] = collect.get("reasoning", "") + ev.text
                        if reason_t0 is None:       # 本段思考起点
                            reason_t0 = time.monotonic()
                    elif isinstance(ev, TextDelta) and reason_t0 is not None:
                        # 正文开始 → 本段思考结束，落定这一段耗时
                        reason_ms += (time.monotonic() - reason_t0) * 1000
                        reason_t0 = None
                    elif isinstance(ev, ModelUsage):
                        # 所有 ModelUsage 都是**逐模型增量**（编排器各子调用 record_usage、
                        # embedding/rerank、ReAct 每步）：按模型累加，合计所有模型即本轮总额（落库同前端）。
                        ubm = collect.setdefault("usage_by_model", {})
                        e = ubm.setdefault(ev.model or "", {"tokens": 0, "cost": 0.0})
                        e["tokens"] += ev.usage.total_tokens
                        e["cost"] += ev.cost_usd or 0.0
                        collect["usage"] = {"tokens": sum(x["tokens"] for x in ubm.values()),
                                            "cost": sum(x["cost"] for x in ubm.values())}
                    elif isinstance(ev, Progress) and ev.scope in ("step_reset", "purged"):
                        # 控制事件：照常下发给在途前端（撤按钮/抖记录靠它们），但不入 progress 列
                        # ——它们不是给用户看的过程记录，留着纯属脏数据。
                        # step_reset 还要顺手把该步上一次的行从**落库副本**里抖掉：前端的实时
                        # 处理器只管内存态，progress 列是这里另攒的——只清实时不清落库，刷新
                        # 后 ChatView 从 progress 列重新取，重复的工具调用又冒出来。
                        # 必须切片就地改：落库读的是外层那个 progress 变量（见 gen() 末尾），
                        # 重新绑定 collect["progress"] 只换了 dict 里的引用，落库的那份纹丝不动。
                        if ev.scope == "step_reset":
                            _sid = ev.text or ""
                            collect["progress"][:] = [
                                p for p in collect["progress"]
                                if p.get("scope") != f"subagent:executor:{_sid}"]
                    elif isinstance(ev, Progress):
                        collect["progress"].append({"scope": ev.scope, "text": ev.text,
                                                    "status": ev.status, "key": ev.key,
                                                    "agent": ev.agent, "detail": ev.detail})
                    # 交付门下缓冲终态事件（不转发）；直通模式转发全部
                    # 门内也转发 TextDelta（用户先看到打字机正文），只压 RunFinished/RunError
                    if passthrough or not isinstance(ev, (RunFinished, RunError)):
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
            # 思考模式按请求透传，但设在 pump() 里、只包主循环（见那里的注释）：设在此处会
            # 一路漏给交付门校验、记忆调和、记忆整合——它们与「我这个问题不用想那么久」无关。
            log.info("聊天开始 msg=%d字 附件=%d 交付门=%s 思考=%s",
                     len(req.message or ""), len(attachment_metas), gate_on, req.think)
            question = req.message
            # 最近几轮对话：交给 judge 解读本轮简短回复（如「A」是从刚给的菜单里选的）。
            # 没有它，judge 会把「A」当成含义不明、反过来怪 AI 没澄清就动手（真实误判）。
            recent_dialogue = _recent_dialogue(history)
            delivered = None
            delivered_sources: list[dict] = []   # 交付那次尝试的权威来源
            errored = False
            used_orchestrator = False    # 本轮是否走编排器路径（其计划终态自洽，不需清单收尾 shim）
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
                # 编排器校验未过、重答前会发 scope=reset 让前端清屏（考试轮）：落库缓冲必须
                # 跟着清，否则最终存的是「被否那版 + 新版」的拼接，与用户屏幕所见不一致。
                # 交付门那条路径由调用方自己 clear（见下方 gate 分支），此处只管编排器发的。
                elif isinstance(ev, Progress) and ev.scope == "reset":
                    parts.clear()
                    store.flush_partial(req.conversation_id, turn_run_id, "")
                return ev

            def _emit_verify(text, status=None, key=None):
                # progress 存的是渲染用的中文文案；结构化判定（哪层没过/重答几次）走
                # verify_trace → conversation_messages.verify 列，供 stats._gate_stats 统计。
                ev = Progress("verify", text, status=status, key=key)
                progress.append({"scope": ev.scope, "text": ev.text,
                                 "status": ev.status, "key": ev.key, "agent": ev.agent})
                return ev

            def _emit_purged(ids: list[str]):
                """告诉在途客户端哪些下载产物已被清理（借 Progress 通道，scope=purged）。

                前端在途的 steps 是从 ToolFinished 事件攒的，早已拿到〔下载ID:x〕；服务端
                _drop_purged_marks 只改了落库那份，不发这条的话，在途界面会留着一个指向
                已删文件的死按钮（刷新才好）。与 sources 一样特判、不入 progress 列。
                """
                if ids:
                    yield Progress("purged", json.dumps(ids, ensure_ascii=False))

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
                if getattr(harness, "orchestrator", None) is not None:
                    # 编排器路径（唯一主流程：装配层恒建 orchestrator，故本轮总走这里）。
                    # 把 harness.orchestrator 当作 loop_obj 交给 _drain（其 run(message) 只 yield 既有
                    # Event 类型），复用同一套事件处理与 SSE 下发。前端"结果校验"开关(req.verify)映射到
                    # 编排器的终局 Critic：开→把关+可重规划，关→跑完一轮直接汇总交付。
                    # 关键：把每请求上下文（base_ctx，含会话历史+全部指引+记忆）、每请求工具表
                    # （registry，含用户级工具）、最近对话注入 run()——否则多轮对话/附件/考试/引用/
                    # 个性化/用户工具全丢。context 只喂给编排器的简单直答（与 ReAct 主路径同源，故也
                    # 同样包一层技能上下文）；registry 喂给简单直答与各执行子步。
                    # 下方 ReAct/交付门两分支仅在 orchestrator 缺失时作惰性兜底（如精简测试注入 None）。
                    used_orchestrator = True
                    collect = {"final": None, "error": None, "steps": steps,
                               "grounding": [], "progress": progress, "usage": None,
                               "reasoning": ""}
                    run_id_a = uuid4().hex
                    store.add_run(req.conversation_id, run_id_a)
                    source_sink.reset()
                    _octx = base_ctx
                    if getattr(harness, "skill_registry", None) is not None:
                        from harness.skills.context import SkillContextManager
                        _octx = SkillContextManager(_octx, harness.skill_registry)
                    # 单步校验不过要重跑时，先把这一版已产出的下载/知识/题目删掉：
                    # 不删则模型重跑会把 save_download 再调一遍，消息下方挂出两个下载按钮，
                    # 其中一个还是判定不合格的那版。user_id 在这里绑定，编排器不必知道用户是谁。
                    _purger = SideEffectPurger(
                        download_store=getattr(harness, "download_store", None),
                        knowledge_service=knowledge_service,
                        question_store=question_store,
                        created_downloads=created_downloads)

                    def _purge_step_fx(fx) -> dict[str, list[str]]:
                        # 只按**实际删掉的**剥标记：删除失败是被吞掉的（清理不该中断回答），
                        # 若按"想删的"剥，磁盘上文件还在而用户的下载入口没了——静默的数据不一致。
                        done = _purger.purge(user_id, fx)
                        # 产物没了，落库 steps 里的机读标记也不能留——否则历史消息重新加载时
                        # 前端照样渲染出下载按钮，点开是已删的文件
                        _drop_purged_marks(steps, done)
                        # 回传**按类分组的 dict** 而非仅下载 id 列表：编排器要据此把对应
                        # 工具名从该步的 done_effects 里摘掉，让模型重做。只回列表的话
                        # 它认不出删的是哪一类，接缝静默失效——文件删了却没人重存。
                        return done

                    _orch_src = SimpleNamespace(
                        run=lambda m: harness.orchestrator.run(
                            m, verify=req.verify, context=_octx, registry=registry,
                            recent_dialogue=recent_dialogue, force_simple=force_simple,
                            in_stateful_exam=in_stateful_exam,
                            purge_side_effects=_purge_step_fx,
                            run_id=run_id_a))   # 事件归到 conversation_runs 登记的 run_id，统计才认
                    # 告诉在途客户端「本轮开了校验门」。必须赶在编排器跑之前发：执行子步的
                    # save_download 远早于编排器那条「结果校验中…」（后者要等所有步骤跑完），
                    # 不先发这条，前端就会在校验还没开始时把生成的文件显示出来。
                    # 这条信号原先只在下方 ReAct+交付门分支里发，而编排器已是唯一主流程，
                    # 于是整套「交付前盖住文件」的机制形同虚设——前端遮挡条件本身是对的。
                    # 仅 req.verify 时发：关校验的轮次编排器一条 verify 事件都不发，前端
                    # 见不到信号即照常显示，不会出现「永远不显示」。
                    # 刻意不落库（不走 _emit_verify）：刷新后由已存的终态记录决定展示即可；
                    # 落库反而会在用户中途停止时留下一条永远转圈的「生成中…」。
                    if req.verify:
                        yield Progress("verify", "生成中…", status="running", key=GATE_OPEN_KEY)
                    async for s in _drain(_orch_src, run_id_a, model_message, True, collect):
                        yield _acc(s)
                    errored = collect["final"] is None
                    if not errored:
                        delivered = collect["final"]
                    elif collect["error"] is not None:
                        partial = "".join(parts).strip()
                        hint = "（本轮未能完成，请重试）"
                        delivered = f"{partial}\n\n{hint}" if partial else hint
                    else:
                        empty_text = "模型未返回任何内容（可能触发内容策略或上游限流），请重试"
                        delivered = f"[出错] {empty_text}"
                        yield RunError(error=empty_text)
                    delivered_sources = source_sink.snapshot()
                    # 回答质量分（轨迹 judge）：编排器已有自己的终局 Critic 把关，这里仅额外打一次
                    # 分层质量分，落 progress 列供「AI 运行统计 · 回答质量」展示，不据此驱动重答。
                    # 仅多步任务（工具步 > 1）才评：单步/无工具无「拆分/多步」可评，跳过省 token
                    # （与旧交付门口径一致）。轨迹 judge 默认关闭，需 enable_trajectory_judge 才生效。
                    #
                    # 必须同时看 req.verify（本轮的结果校验开关）。此前只看服务端开关，于是用户
                    # 关掉开关后：后端确实不做终局 review、一条 scope=verify 都不发，但质量分照
                    # 发——前端 VerifyBadge 的 kind 判据是「有 verify 事件 **或** 有 quality」，
                    # 被 quality 命中，主行照样显示「结果校验通过」。用户关掉了校验，却被告知
                    # 结果校验通过了。质量分只是打分、不驱动重答，冒充不了「把过关」。
                    if (not errored and req.verify and trajectory_judge is not None
                            and config.enable_trajectory_judge and delivered
                            and len(collect["steps"]) > 1):
                        try:
                            tscore = await trajectory_judge.score(
                                question, _plan_text(progress),
                                _tool_exec_summary(collect["steps"]), delivered)
                            yield _emit_quality(tscore)
                        except Exception as e:   # 质量分是附加统计，失败绝不影响正常交付
                            log.warning("轨迹 judge 打分失败（不影响交付）：%s", e, exc_info=True)
                elif not gate_on:
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

                    def _purge_side_effects(fx) -> list[str]:
                        """删掉未通过轮的产物，返回被删的下载 id（供告知在途客户端）。"""
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
                        # 产物没了，落库的步骤结果里那些机读标记也不能留 —— 否则前端照样
                        # 渲染出下载按钮，点开是已删的文件（steps 累积了本轮全部尝试）
                        _drop_purged_marks(steps, fx)
                        return list(fx["download"])

                    def _settle_stale_fx(stale, cur) -> list[str]:
                        """交付时结算未通过轮的产物：被本版取代的删掉，本版没重做的留下。

                        返回被删的下载 id（供 _emit_purged 告知在途客户端）。
                        """
                        _purge, _keep = _split_stale_fx(stale, cur)
                        _mark_carried_over(steps, _keep)
                        if any(_keep.values()):
                            # 模型没照 _redo_fx_note 的指令重做 —— 提示词管不住的那种情况。
                            # 兜底已保住产物，但这条日志是唯一能统计其发生频率的地方。
                            log.warning(
                                "交付版未重做上一版的副作用产物，已保留旧产物 conv=%s 保留=%s",
                                req.conversation_id,
                                {k: len(v) for k, v in _keep.items() if v})
                        return _purge_side_effects(_purge)

                    # 告诉在途客户端「本轮开了校验门」。必须赶在 agent 跑之前发：工具执行
                    # 先于首个「校验中…」，前端要据此在交付前一直不显示生成的文件——未通过会
                    # 重答、届时这些产物被 _purge_side_effects 清掉，提前显示等于给用户一个
                    # 马上失效的下载按钮。
                    # key 固定为 GATE_OPEN_KEY（前端 VerifyBadge.tsx 同名常量）：这只是个门已开
                    # 的信号，不是一条校验进展——此刻模型连初稿都还没生成，没有任何东西可校验。
                    # 前端据此把它排除在校验徽章之外，否则回答刚起头就转圈谎称「正在校验」。
                    # 刻意不落库（不走 _emit_verify）：刷新后由已存的终态记录决定展示即可；
                    # 落库反而会在用户中途停止时留下一条永远转圈的「生成中…」。
                    yield Progress("verify", "生成中…", status="running", key=GATE_OPEN_KEY)

                    for attempt in range(max_attempts):
                        msg = model_message if corrective is None else corrective
                        collect = {"final": None, "error": None, "steps": [],
                                   "grounding": [], "progress": progress, "usage": None,
                                   "reasoning": ""}
                        run_id_a = uuid4().hex
                        store.add_run(req.conversation_id, run_id_a)
                        source_sink.reset()   # 每次尝试重置，交付时快照该次来源
                        # 门内 TextDelta 现在实时流式：经 _acc 累积进 parts（供刷新还原本轮已见文本）
                        async for s in _drain(_new_loop(run_id_a), run_id_a, msg, False, collect):
                            yield _acc(s)
                        steps.extend(collect["steps"])
                        draft = (collect["final"] or "").strip()
                        cur_fx = _side_effect_ids(collect["steps"])   # 本轮生成的副作用产物

                        ekey = uuid4().hex
                        # 文案须自报层级：徽章原样显示它，而终态行都写明了是哪层（「结果校验
                        # 通过」/「步骤校验未通过」）——唯独进行中只说「校验中…」，用户就看不出
                        # 转圈的是交付门还是每步校验。交付门属结果层，故这里明写「结果校验中…」。
                        yield _emit_verify("结果校验中…", status="running", key=ekey)
                        if not draft:
                            verdict = Verdict(ok=False, failed=["empty"],
                                              critique=collect["error"] or "本轮未产出答案",
                                              summary="未产出答案")
                        else:
                            stoken = set_sandbox_conv(req.conversation_id)
                            try:
                                # 记源暂停：校验器跑答案里的代码块、核对引用链接，用的是同一个
                                # 已包记源层的 registry，否则这些后台调用会冒充成模型的「参考
                                # 来源」——用户从没看见 AI 执行过它们。
                                with source_sink.paused():
                                    verdict = await verifier.verify(
                                        question, draft, collect["grounding"], registry,
                                        steps=collect["steps"], recent_dialogue=recent_dialogue)
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
                            # 交付本轮：只删被本版取代的旧产物，本版没重做的那类予以保留
                            for _ev in _emit_purged(_settle_stale_fx(stale_fx, cur_fx)):
                                yield _ev
                            break
                        # error 事件的 text 携带完整原因（critique），供前端展开显示
                        # error 事件 text：中文层名 + 完整原因，供前端展示「哪层没过 + 为什么」
                        layers = failed_layers_zh(verdict.failed) or "校验"
                        reason = (f"{layers}未通过"
                                  + (f"：{verdict.critique}" if verdict.critique else ""))
                        yield _emit_verify(reason, status="error", key=ekey)
                        if attempt == max_attempts - 1:      # 用尽次数 → 降级交付
                            verify_trace["degraded"] = True
                            # 最后一版已流式显示给用户，保留原样、不再在正文前拼 ⚠️ 告示
                            # （那会导致同一段文字清屏重打）——未过由红色「结果校验未通过」徽章表达。
                            delivered = draft or "（本轮未完成）"
                            delivered_sources = source_sink.snapshot()
                            errored = not draft
                            # 降级交付本轮：同样只删被本版取代的那类旧产物
                            for _ev in _emit_purged(_settle_stale_fx(stale_fx, cur_fx)):
                                yield _ev
                            break
                        for _k in stale_fx:   # 本轮未通过、将重答，其副作用产物作废待清理
                            stale_fx[_k] += cur_fx[_k]
                        yield _emit_verify("重答中…", status="running")
                        # 清屏：上一版正文已流式显示给用户，重答前清空，让新版从头打字机输出。
                        # 同步重置 parts 与落库占位——否则刷新恰好落在重答间隙时会看到旧版残留。
                        parts.clear()
                        store.flush_partial(req.conversation_id, turn_run_id, "")
                        yield Progress("reset", "")
                        corrective = (f"你上一版回答未通过自动校验。问题：{verdict.critique}。"
                                      f"请针对性修正后，重新完整回答原问题：{question}"
                                      + _redo_fx_note(collect["steps"]))

                    emit_gate_span(_tracer, verify_trace, gate_t0)
                    # 交付：正文已在生成时逐字流式给用户，无需重发（避免清屏重打同一段）。
                    # 仅当屏上确实空白（降级且本轮空产出）才补一句兜底文案，再合成 RunFinished。
                    delivered = delivered or "（本轮未完成）"
                    if not "".join(parts).strip():
                        for chunk in _chunks(delivered):
                            yield _acc(TextDelta(text=chunk))
                    yield RunFinished(message=Message(role=Role.ASSISTANT, content=delivered))

                # 清单收尾：交付门开与不开两条路径都会漏，故放在二者汇合处。仅当模型真的
                # 没把清单更新完才会花那一次调用（实测约 1/6 的多步任务会）。答案已定稿，
                # 这里只动清单。errored 时不补：运行都没跑完，那些步骤本就该显示为未完成。
                # 只有**编排器发的**计划才跳过：其状态机保证每步有终态（done/failed/skipped），
                # 清单天然自洽（spec §6）。而简单直答里模型自己调 update_plan 发的 ReAct 清单
                # 编排器状态机根本没管过——按 used_orchestrator 短路会把它一并跳过，它就永远
                # 停在最后一次自述的状态上，前端渲染成一排 unknown + 「清单未更新完」。
                if not errored and not _plan_from_orchestrator(progress):
                    _plan_ev = await _finalize_stale_plan(
                        _plan_finalizer, progress, steps, plan_trace)
                    if _plan_ev is not None:
                        # 与 update_plan 同 key → 前端同键覆盖；同时写进 progress 列，
                        # 否则刷新后又退回那份没收尾的（progress 才是刷新后的唯一依据）
                        progress.append({"scope": _plan_ev.scope, "text": _plan_ev.text,
                                         "status": _plan_ev.status, "key": _plan_ev.key,
                                         "agent": _plan_ev.agent})
                        yield _plan_ev
                # 正常完成：先把来源推给在途客户端，再 UPDATE 占位消息为最终态
                if delivered_sources:
                    yield _emit_sources(delivered_sources)
                status = "error" if errored else "done"
                final_content = delivered or "".join(parts) or "（本轮未完成）"
                _usage = collect.get("usage") or {}
                _elapsed = round((time.time() - turn_start) * 1000)
                # 编排器的终局校验结论（结构化）：Progress 里只有给人看的中文，统计侧解不出
                # 「过没过 / 重答几次 / 拦在哪层」。放在落库前提取，不依赖上游分支的执行顺序。
                # 交付门分支若已自行填过 verify_trace，则不覆盖（那条路有更细的逐层记录）。
                if verify_trace is None:
                    for _p in progress or []:
                        if _p.get("key") == VERIFY_TRACE_KEY and _p.get("detail"):
                            verify_trace = _p["detail"]
                            break
                store.finish_turn(req.conversation_id, turn_run_id, final_content,
                                  steps=steps or None, progress=progress or None,
                                  status=status, sources=delivered_sources or None,
                                  tokens=_usage.get("tokens"), cost=_usage.get("cost"),
                                  elapsed_ms=_elapsed, reasoning=collect.get("reasoning") or None,
                                  reasoning_ms=collect.get("reasoning_ms"),
                                  verify=verify_trace,
                                  context={**ctx_trace, "plan": plan_trace} if plan_trace
                                  else (ctx_trace or None))
                log.info("聊天完成 status=%s 耗时%dms tokens=%s 工具%d次 来源%d条 重答%s次",
                         status, _elapsed, _usage.get("tokens"), len(steps),
                         len(delivered_sources),
                         verify_trace["retries"] if verify_trace else "-")
                # L3 记忆写入 + 整合：挪到后台，不阻塞本轮完成（status=done 已在上面落库、
                # 流随 gen() 返回即刻关闭）。此前在此 await 记忆写入会让「校验通过」后仍转圈半天。
                if not errored and final_content:
                    _spawn_post_turn(
                        req.conversation_id, len(history),
                        f"用户：{req.message}\n助手：{final_content}")
            except asyncio.CancelledError:
                # stop：落已生成部分 + stopped，再放行取消
                store.finish_turn(
                    req.conversation_id, turn_run_id,
                    delivered or "".join(parts) or "（已停止）",
                    steps=steps or None, progress=progress or None, status="stopped",
                    elapsed_ms=round((time.time() - turn_start) * 1000),
                    verify=verify_trace,   # 中断时保留已发生的重答记录
                    context=ctx_trace or None)
                raise
            except Exception as e:  # noqa: BLE001  意外异常也要把占位落成 error，别永远 streaming
                store.finish_turn(
                    req.conversation_id, turn_run_id,
                    delivered or "".join(parts) or "（本轮未能完成，请重试）",
                    steps=steps or None, progress=progress or None, status="error",
                    elapsed_ms=round((time.time() - turn_start) * 1000),
                    verify=verify_trace,   # 出错时保留已发生的重答记录
                    context=ctx_trace or None)
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

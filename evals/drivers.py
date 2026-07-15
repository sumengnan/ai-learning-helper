# evals/drivers.py
"""「怎么跑一个 case」—— 把被测对象（SUT）跑起来，产出待打分的东西。

协作者一律显式注入，driver 绝不自己 build_harness：这样 mock 层（tests/evals/ 传
conftest 的 MockModelClient / MockEmbeddingClient）与真实层（evals/cli.py 传
build_harness 的产物）走完全相同的代码路径。零网络因此是结构性保证而非纪律性保证 ——
不是「不去调真实端点」，是根本没有真实 client 可调。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from app.config import AppConfig
from app.exam_grader import grade_objective, grade_short, parse_choice
from app.verify import AnswerVerifier
from harness.context.manager import ContextManager
from harness.events import (RunError, RunFinished, TextDelta, ToolFinished, ToolStarted)
from harness.loop.agent_loop import AgentLoop
from harness.memory.eval import GoldenCase, evaluate
from harness.memory.record import MemoryFilter, MemoryRecord, MemType
from harness.memory.reranker import NoOpReranker
from harness.memory.retriever import RetrievalConfig, Retriever
from harness.memory.sqlite_backend import SqliteVecBackend
from harness.tools.base import ToolError

from .schema import BAD_JSON, RAISE

_OWNER = "eval-owner"
_KIND = "eval"


def scripted_complete(mapping: dict):
    """按 system prompt 里的关键词返回预设 JSON 串。复刻 tests/app/test_verify.py:15 的 _fake_complete。

    两个特殊值让数据集能表达线上真实故障，把它们钉成回归 case：
      RAISE     → 抛异常（端点挂了）
      BAD_JSON  → 返回非法 JSON（模型不吐 JSON）
    都没命中关键词时返回 "{}"，与既有测试替身一致。
    """
    async def complete(system: str, user: str) -> str:
        for key, payload in mapping.items():
            if key in system:
                if payload == RAISE:
                    raise RuntimeError("scripted judge failure")
                if payload == BAD_JSON:
                    return "这不是 JSON"
                return json.dumps(payload, ensure_ascii=False)
        return "{}"
    return complete


@dataclass
class AgentTrace:
    """一次 agent 运行的产物。steps 形状对齐 app/api/chat.py 的 collect["steps"]
    （{"tool","args","result","is_error"}），好让 app/verify.py::_tool_exec_summary 直接吃 ——
    离线判分喂给 judge 的上下文因此与线上逐字一致，分数才可比。"""
    final: str = ""
    tools: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    plan: str = ""
    error: str = ""
    elapsed_ms: int = 0


class AgentDriver:
    """跑真实 AgentLoop（慢层）。

    协作者全部注入、绝不自己 build_harness：mock 层（tests/evals/ 传 MockModelClient）与
    真实层（cli 传 build_harness 的 client/registry）因此走完全相同的代码路径。
    事件收集逻辑对齐 app/api/chat.py:344-392。
    """

    def __init__(self, *, client, registry, system_prompt: str = "",
                 max_steps: int = 10, model_name: str = "") -> None:
        self._client = client
        self._registry = registry
        self._system_prompt = system_prompt
        self._max_steps = max_steps
        self._model_name = model_name

    async def run(self, case) -> AgentTrace:
        sp = case.input.system_prompt or self._system_prompt
        loop = AgentLoop(client=self._client, registry=self._registry,
                         context=ContextManager(sp), max_steps=self._max_steps,
                         model_name=self._model_name)
        trace = AgentTrace()
        by_id: dict[str, dict] = {}
        parts: list[str] = []
        t0 = time.monotonic()
        async for ev in loop.run(case.input.message):
            if isinstance(ev, TextDelta):
                parts.append(ev.text)
            elif isinstance(ev, RunFinished):
                trace.final = ev.message.content or ""
            elif isinstance(ev, RunError):
                trace.error = ev.error
            elif isinstance(ev, ToolStarted):
                tc = ev.tool_call
                st = {"tool": tc.name, "args": tc.arguments}
                trace.steps.append(st)
                by_id[tc.id] = st
                trace.tools.append(tc.name)
            elif isinstance(ev, ToolFinished):
                st = by_id.get(ev.result.tool_call_id)
                if st is not None:
                    st["result"] = ev.result.content
                    st["is_error"] = ev.result.is_error
        trace.elapsed_ms = int((time.monotonic() - t0) * 1000)
        # RunFinished 没来（跑挂/超步）时退回累积的流式文本，让打分器仍有东西可看
        trace.final = trace.final or "".join(parts)
        # update_plan 工具的入参即任务拆分，供轨迹 judge 用
        trace.plan = next((s.get("args", "") for s in trace.steps
                           if s.get("tool") == "update_plan"), "")
        return trace


class ExamGradeDriver:
    """驱动 app/exam_grader.py：客观题走 parse_choice + grade_objective，简答走 grade_short。

    judge_complete 为 None 时（mock 层）按 case.input.judge_returns 现造脚本化 judge；
    真实层可注入 build_judge_completer 的产物。
    """

    def __init__(self, *, judge_complete=None) -> None:
        self._judge_complete = judge_complete

    async def run(self, case) -> dict:
        q = case.input.question
        if q.get("type") == "short":
            judge = self._judge_complete or scripted_complete(case.input.judge_returns)
            correct, feedback = await grade_short(judge, q, case.input.user_text)
            return {"parsed": None, "correct": correct, "feedback": feedback}
        parsed = parse_choice(case.input.user_text, q)
        # 解析不出/有歧义 → 线上是「要求重答，不判不存不推进」，故此处判分也无意义
        correct = grade_objective(q, parsed) if parsed is not None else None
        return {"parsed": parsed, "correct": correct}


class _StubCodeTool:
    """假代码工具：run 成功，或按需抛 ToolError。搬自 tests/app/test_verify.py:85。"""

    class Params:
        def __init__(self, code, version=None):
            self.code = code

    def __init__(self, fail: bool = False) -> None:
        self._fail = fail

    async def run(self, params):
        if self._fail:
            raise ToolError("exit_code=1\nstderr:\nSyntaxError")
        return "exit_code=0\nstdout:\nok"


class _StubRegistry:
    def __init__(self, tools: dict) -> None:
        self._t = tools

    def get(self, name):
        return self._t.get(name)


class GateDriver:
    """驱动 app/verify.py::AnswerVerifier.verify。

    注意「LLM 失败即放行」是 SUT 刻意的线上约定，不是 bug —— 数据集里有专门的 case
    （judge_returns 用 RAISE / BAD_JSON）把它钉成回归测试。这与 evals 自己的 judge
    打分器必须显式报错并不矛盾：那是打分器，这是被测对象。
    """

    def __init__(self, *, base_config: AppConfig | None = None) -> None:
        self._base = base_config or AppConfig(api_key="k", app_db_path=":memory:",
                                              _env_file=None)

    async def run(self, case):
        cfg = self._base.model_copy(update=case.input.config_overrides)
        # 无桩工具 → registry 为 None → code/facts 检查自动跳过（无工具可跑）
        stubs = {name: _StubCodeTool(fail=(mode == "fail"))
                 for name, mode in case.input.stub_tools.items()}
        registry = _StubRegistry(stubs) if stubs else None
        verifier = AnswerVerifier(scripted_complete(case.input.judge_returns), cfg)
        return await verifier.verify(case.input.question, case.input.answer,
                                     case.input.grounding, registry,
                                     steps=case.input.steps)


class RetrievalDriver:
    """按语料建 :memory: 库，包 harness.memory.eval.evaluate 算单条 case 的 hit@k / mrr。

    语料只在首个 case 时 seed 一次（embed 是 async，故懒加载而非在 __init__ 里做）。
    """

    def __init__(self, *, embedder, corpus: list[tuple[str, str]],
                 retrieval_config: RetrievalConfig | None = None,
                 k: int = 3, dimension: int = 64) -> None:
        self._embedder = embedder
        self._corpus = corpus
        self._cfg = retrieval_config or RetrievalConfig()
        self._k = k
        self._dim = dimension
        self._retriever = None

    async def _ensure(self) -> None:
        if self._retriever is not None:
            return
        backend = SqliteVecBackend(":memory:", dimension=self._dim)
        for rid, text in self._corpus:
            vec = (await self._embedder.embed([text]))[0]
            backend.upsert([MemoryRecord(owner_id=_OWNER, kind=_KIND, mem_type=MemType.SEMANTIC,
                                         text=text, embedding=vec, id=rid)])
        self._retriever = Retriever(backend, self._embedder, NoOpReranker(), self._cfg)

    async def run(self, case) -> dict:
        await self._ensure()
        golden = [GoldenCase(query=case.input.query,
                             relevant_ids=set(case.expect.relevant_ids))]
        return await evaluate(self._retriever, MemoryFilter(owner_id=_OWNER), golden, self._k)

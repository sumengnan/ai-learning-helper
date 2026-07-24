import pytest
from harness.events import Progress
from harness.llm.base import StreamChunk
from pydantic import BaseModel
from harness.tools.base import Tool, ToolError, ToolRegistry
from harness.types import ToolOutput
from app.orchestration.executor import Executor, StepArtifact
from app.orchestration.plan import Artifact, PlanStep


def _step(deps=()):
    return PlanStep(id="s1", description="回答质数定义", expected="质数定义",
                    depends_on=list(deps))


def _text_only_turns(text):
    # 一轮：吐正文 + done（无工具）→ AgentLoop 直接 RunFinished
    return [[StreamChunk(type="text", text=text), StreamChunk(type="done")]]


async def _collect(gen):
    events, artifact = [], None
    async for ev in gen:
        if isinstance(ev, StepArtifact):
            artifact = ev.artifact
            # 便于测试直接查 artifact.error/side_effects，而不必额外返回整个 StepArtifact 信号
            artifact.error = ev.error
            artifact.side_effects = ev.side_effects
        else:
            events.append(ev)
    return events, artifact


async def test_executor_produces_artifact_from_final_text(make_mock):
    client = make_mock(_text_only_turns("质数是只有1和自身两个因子的自然数"))
    ex = Executor(client=client, registry=ToolRegistry(), system_prompt="你是执行者", model="m", max_steps=3)
    events, artifact = await _collect(ex.execute(_step(), {}))
    assert isinstance(artifact, Artifact)
    assert "质数" in artifact.summary


async def test_executor_emits_progress_not_textdelta(make_mock):
    """执行者内部产出不得作为 TextDelta 泄露给用户。用户可见的只有 Progress；另透传原始
    ToolStarted/ToolFinished/StepStarted 供「AI 运行统计」聚合（非用户可见文本，前端有计划时不渲染）。"""
    client = make_mock(_text_only_turns("中间产出"))
    ex = Executor(client=client, registry=ToolRegistry(), system_prompt="sp", model="m", max_steps=3)
    events, _ = await _collect(ex.execute(_step(), {}))
    from harness.events import TextDelta, StepStarted, ToolStarted, ToolFinished
    assert not any(isinstance(e, TextDelta) for e in events)          # 绝不泄露正文
    assert all(isinstance(e, (Progress, StepStarted, ToolStarted, ToolFinished))
               for e in events)                                       # 仅 Progress + 统计埋点


def test_build_prompt_includes_deps_and_hint():
    from app.orchestration.executor import _build_prompt
    p = _build_prompt(_step(deps=["s0"]), {"s0": Artifact(summary="前置：X=42")}, hint="补充示例")
    assert "回答质数定义" in p and "质数定义" in p
    assert "s0" in p and "前置：X=42" in p
    # 步骤 id 不能作为前缀紧贴正文，否则模型会把「[s0] 」连同内容一起抄进产出
    assert "[s0] 前置：X=42" not in p
    assert "补充示例" in p


def test_build_prompt_nudges_economical_search():
    """提示词含"少搜、够了就作答"的节流引导，减少每步的联网/工具往返。"""
    from app.orchestration.executor import _build_prompt
    p = _build_prompt(_step(), {})
    assert "够了" in p or "最多" in p or "不必反复" in p


async def test_executor_disable_thinking_sets_override():
    """disable_thinking=True → 子步执行期间强制 enable_thinking=False（省思考链延迟）。"""
    from harness.llm.openai_compat import get_extra_body_override
    seen = {}
    class ProbeClient:
        async def stream(self, messages, schemas):
            cur = get_extra_body_override() or {}
            seen["thinking"] = cur.get("enable_thinking", "unset")
            yield StreamChunk(type="text", text="ok")
            yield StreamChunk(type="done")
    ex = Executor(client=ProbeClient(), registry=ToolRegistry(), system_prompt="sp",
                  model="m", max_steps=1, disable_thinking=True)
    await _collect(ex.execute(_step(), {}))
    assert seen["thinking"] is False


async def test_executor_prompt_steers_to_web_search():
    """执行子步的系统提示词应引导优先用联网搜索工具、少用 http_request 抓网页。"""
    seen = {}
    class ProbeClient:
        async def stream(self, messages, schemas):
            parts = []
            for m in messages:
                c = getattr(m, "content", None)
                if c is None and isinstance(m, dict):
                    c = m.get("content")
                if isinstance(c, str):
                    parts.append(c)
            seen["sys"] = "\n".join(parts)
            yield StreamChunk(type="text", text="ok")
            yield StreamChunk(type="done")
    ex = Executor(client=ProbeClient(), registry=ToolRegistry(), system_prompt="你是执行者",
                  model="m", max_steps=1)
    await _collect(ex.execute(_step(), {}))
    assert "搜索" in seen["sys"] and "http_request" in seen["sys"]   # 明确工具偏好引导
    assert "你是执行者" in seen["sys"]                                # 基座 prompt 仍在


async def test_executor_thinking_untouched_by_default():
    """默认 disable_thinking=False → 不动 override（沿用外层上下文）。"""
    from harness.llm.openai_compat import get_extra_body_override
    seen = {}
    class ProbeClient:
        async def stream(self, messages, schemas):
            seen["thinking"] = (get_extra_body_override() or {}).get("enable_thinking", "unset")
            yield StreamChunk(type="text", text="ok")
            yield StreamChunk(type="done")
    ex = Executor(client=ProbeClient(), registry=ToolRegistry(), system_prompt="sp", model="m", max_steps=1)
    await _collect(ex.execute(_step(), {}))
    assert seen["thinking"] == "unset"


def _tool_then_done_turns():
    from harness.llm.base import ToolCallDelta
    return [
        [StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
            index=0, id="c1", name="calculator", arguments='{"expression": "1+1"}')),
         StreamChunk(type="done")],
        [StreamChunk(type="text", text="算完了"), StreamChunk(type="done")],
    ]


async def test_executor_tool_call_emits_progress(make_mock):
    from harness.tools.builtins.calculator import CalculatorTool
    reg = ToolRegistry(); reg.register(CalculatorTool())
    client = make_mock(_tool_then_done_turns())
    ex = Executor(client=client, registry=reg, system_prompt="sp", model="m", max_steps=3)
    events, artifact = await _collect(ex.execute(_step(), {}))
    progs = [e for e in events if isinstance(e, Progress)]
    assert any(p.scope == "subagent:executor:s1" for p in progs)
    assert any(p.status == "running" for p in progs)
    assert any(p.status == "ok" for p in progs)
    assert "算完了" in artifact.summary


async def test_executor_passes_through_raw_tool_events_for_stats(make_mock):
    """执行子步透传原始 ToolStarted/ToolFinished/StepStarted（不止转 Progress）：供 sink 落
    trajectory、再进「AI 运行统计」聚合能力/步数。此前只发 Progress，这些埋点在编排器复杂路径下全丢。"""
    from harness.events import StepStarted, ToolFinished, ToolStarted
    from harness.tools.builtins.calculator import CalculatorTool
    reg = ToolRegistry(); reg.register(CalculatorTool())
    client = make_mock(_tool_then_done_turns())
    ex = Executor(client=client, registry=reg, system_prompt="sp", model="m", max_steps=3)
    events, _ = await _collect(ex.execute(_step(), {}))
    ts = [e for e in events if isinstance(e, ToolStarted)]
    tf = [e for e in events if isinstance(e, ToolFinished)]
    assert ts and ts[0].tool_call.name == "calculator"       # 原始 ToolStarted 透传
    assert tf and tf[0].result.tool_call_id == "c1"           # 原始 ToolFinished 透传
    assert any(isinstance(e, StepStarted) for e in events)    # StepStarted 透传（供步数统计）


def _always_tool_turns():
    from harness.llm.base import ToolCallDelta
    return [[StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
        index=0, id="c1", name="calculator", arguments='{"expression":"1+1"}')),
        StreamChunk(type="done")]]


async def test_executor_emits_step_header_and_tool_detail(make_mock):
    """首个进度是步骤描述头行；工具行带 detail（tool+args），完成行 detail 带 result+is_error 且保留 args。"""
    from harness.tools.builtins.calculator import CalculatorTool
    reg = ToolRegistry(); reg.register(CalculatorTool())
    client = make_mock(_tool_then_done_turns())
    ex = Executor(client=client, registry=reg, system_prompt="sp", model="m", max_steps=3)
    events, _ = await _collect(ex.execute(_step(), {}))
    progs = [e for e in events if isinstance(e, Progress)]
    # (a) 头行：文字=步骤描述，key=__hdr__:s1，无 detail
    assert progs[0].text == "回答质数定义" and progs[0].key == "__hdr__:s1"
    assert progs[0].detail is None
    # (b) 工具开始行 detail 带 tool + args
    started = [p for p in progs if p.status == "running" and p.detail]
    assert any(p.detail["tool"] == "calculator" and p.detail["args"] == {"expression": "1+1"}
               for p in started)
    # (c) 工具完成行 detail 带 result + is_error，且仍保留 args（前端按 key 合并只留最后一条）
    finished = [p for p in progs if p.status in ("ok", "error") and p.detail and "result" in p.detail]
    assert finished and finished[-1].detail["args"] == {"expression": "1+1"}
    assert "is_error" in finished[-1].detail


async def test_executor_runerror_sets_error_and_empty_summary(make_mock):
    from harness.tools.builtins.calculator import CalculatorTool
    reg = ToolRegistry(); reg.register(CalculatorTool())
    client = make_mock(_always_tool_turns())
    ex = Executor(client=client, registry=reg, system_prompt="sp", model="m", max_steps=1)
    events, artifact = await _collect(ex.execute(_step(), {}))
    assert artifact.error is not None
    assert artifact.summary == ""


def test_system_with_guide_appends_prerendered_sandbox_text():
    """执行子步系统提示词应把装配层预渲染的沙箱指引原样附上；空则不附。"""
    from app.orchestration.executor import _system_with_guide
    with_g = _system_with_guide("基座提示", "\n\n【沙箱工作目录】cwd 是 /workspace")
    assert "/workspace" in with_g and "工作目录" in with_g
    without = _system_with_guide("基座提示", "")
    assert "工作目录" not in without   # 无沙箱不提，避免误导


def test_executor_system_prompt_includes_clarify_guide():
    """执行子步系统提示词应带「信息不足先问、不要猜」指引（无论有无沙箱）。"""
    from app.orchestration.executor import _system_with_guide, CLARIFY_GUIDE
    assert "信息不足先问" in CLARIFY_GUIDE
    assert "信息不足先问" in _system_with_guide("基座提示", "")
    assert "信息不足先问" in _system_with_guide("基座提示", "\n\n【沙箱工作目录】x")


def test_executor_system_prompt_includes_search_guidance():
    """执行子步系统提示词必须带「联网检索的提问方式」指引。

    覆盖 Bug：AI 把用户原话整句照抄进 MCP 搜索的 query。这段指引原先只拼在
    harness.system_prompt 上——主聊天与简单直答经 context 拿得到，而执行子步的 base 是裸的
    config.app_system_prompt（见 assembly.py 构造 Executor 处）。多步任务里联网检索恰恰归
    子步做，于是指引根本没到真正调工具的那个上下文，只剩 EXECUTOR_GUIDE 的「优先用搜索
    工具」——教了用什么，没教怎么写 query。
    """
    from app.orchestration.executor import _system_with_guide
    for sandbox_text in ("", "\n\n【沙箱工作目录】x"):
        sp = _system_with_guide("基座提示", sandbox_text)
        assert "联网检索的提问方式" in sp
        assert "时间必须锚定" in sp        # 相对时间要换算成绝对年份
        assert "正交的子查询" in sp        # 宽泛问题要拆
        assert "不要为拆而拆" in sp        # 简单事实查询别被拖慢


def test_search_guidance_date_wording_fits_both_contexts():
    """指引里对「今天」的引用不能钉死某一处的写法。

    主聊天用 _today_guide() 的「【当前日期】今天是 X 年 X 月 X 日」，执行子步用
    _system_with_guide 的「今日日期：YYYY-MM-DD」——措辞若只认前者，子步里就成了悬空引用。
    """
    from app.search_guidance import SEARCH_SYSTEM_GUIDANCE
    assert "【当前日期】" not in SEARCH_SYSTEM_GUIDANCE
    assert "今天日期" in SEARCH_SYSTEM_GUIDANCE


def test_orchestrator_simple_answer_carries_clarify_guide():
    """全编排器/编排器模式下简单直答面向用户，其系统提示应带澄清指引。"""
    from app.orchestration.orchestrator import Orchestrator
    from app.orchestration.executor import CLARIFY_GUIDE
    import inspect
    src = inspect.getsource(Orchestrator._simple_answer)
    assert "CLARIFY_GUIDE" in src   # 简单直答上下文拼接了澄清指引
    assert "信息不足先问" in CLARIFY_GUIDE


def _sbx_cfg(**kw):
    from app.config import AppConfig
    return AppConfig(api_key="k", app_db_path=":memory:", **kw)


def test_sandbox_guide_local_only_workdir_and_forbids_host_paths():
    from app.sandbox_manager import sandbox_guide
    g = sandbox_guide(_sbx_cfg(sandbox_backend="local"))
    assert "/workspace" in g and "uploads" in g
    assert "宿主机" in g          # 明确禁止用宿主机路径
    assert "镜像" not in g         # 非 docker 不提镜像/联网


def test_sandbox_guide_docker_reports_images_and_network():
    """docker 后端：如实报告 shell 容器与各语言容器的镜像、联网，并据网络说明能否装依赖。"""
    from app.sandbox_manager import sandbox_guide
    g = sandbox_guide(_sbx_cfg(
        sandbox_backend="docker", sandbox_shell_image="quay.io/centos/centos:stream9",
        sandbox_network="bridge"))
    assert "quay.io/centos/centos:stream9" in g   # shell 容器镜像
    assert "python:3.12-slim" in g                # 语言容器镜像（默认 lang_images）
    assert "shell 容器" in g and "语言" in g       # 按语言各自独立容器
    assert "可联网" in g                          # bridge → 可联网
    assert "pip install" in g                     # 联网环境可自行装包的指引
    assert "language" in g                        # 文件工具 language 参数落对应容器的说明
    # 权限硬约束：加固沙箱非 root，apt/全局装会 Permission denied——必须如实告知，否则模型照旧文案去 apt 白撞
    assert "非 root" in g and "apt" in g
    assert "--target" in g                        # 给出唯一可行路径：装到可写工作目录


def test_sandbox_guide_docker_offline_says_cannot_install():
    """禁网（sandbox_network=none）时，指引应说明装不了包、只能用预装。"""
    from app.sandbox_manager import sandbox_guide
    g = sandbox_guide(_sbx_cfg(sandbox_backend="docker", sandbox_network="none"))
    assert "禁止联网" in g
    assert "预装" in g and "apt" in g             # 禁网 + 非 root，一律装不了


def test_build_prompt_does_not_glue_step_id_to_content():
    """前置产出的步骤 id 必须与正文分行。

    实例：第3步的提示词里出现「[s2] # AI发展与应用总结\\n\\n## 1. ...」，模型复用这份
    内容时把「[s2] 」前缀一起抄了出来，最终存进用户下载的文件开头。
    """
    from app.orchestration.executor import _build_prompt
    art = Artifact(summary="# AI发展与应用总结\n\n## 1. 概述\n正文")
    p = _build_prompt(_step(deps=["s2"]), {"s2": art})
    assert "[s2] # AI发展与应用总结" not in p     # 前缀不得紧贴正文首行
    assert "# AI发展与应用总结" in p              # 内容本身仍在
    assert "s2" in p                              # 仍能看出这是哪一步的产出


# ---------- 子步必须看得见用户原话 ----------

def test_step_prompt_carries_user_goal():
    """回归：用户发「翻译这段：<日志>」，子步回「请提供文本」。

    子步的上下文是全新的 ContextManager（只有系统提示词、无对话历史），execute() 此前
    也不收用户消息。计划步的 description 是对任务的**转述**，原料不在里面——规划器写出
    「将提供的英文文本翻译成中文」，子步拿到的就只有这句，文本本身丢了。
    终局校验连判三次未通过，判得没错：活确实没干成。
    """
    from app.orchestration.executor import _build_prompt
    from app.orchestration.plan import PlanStep
    step = PlanStep(id="s1", description="将提供的英文文本翻译成中文",
                    expected="写进答复正文的中文翻译", depends_on=[])
    goal = "翻译这段：Node 20 is being deprecated. Removing builder"
    p = _build_prompt(step, {}, "", goal)
    assert "Node 20 is being deprecated" in p, "要翻译的原文必须进子步提示词"
    assert "Removing builder" in p
    assert "将提供的英文文本翻译成中文" in p          # 子任务描述仍在


def test_long_goal_is_capped_not_dropped():
    """超长原文截断而非丢弃：丢了等于回到「请提供文本」，截断至少还能干大半。"""
    from app.orchestration.executor import _GOAL_MAX, _build_prompt
    from app.orchestration.plan import PlanStep
    step = PlanStep(id="s1", description="翻译", expected="译文", depends_on=[])
    p = _build_prompt(step, {}, "", "开头标记" + "x" * (_GOAL_MAX * 2))
    assert "开头标记" in p, "保头：原文开头不能被截掉"
    assert "已截断" in p, "截断要显式说明，别让模型以为原文就这么短"
    assert len(p) < _GOAL_MAX * 2


def test_no_goal_keeps_prompt_unchanged():
    """不传 goal 时不加空壳段落（旧调用方与测试不受影响）。"""
    from app.orchestration.executor import _build_prompt
    from app.orchestration.plan import PlanStep
    step = PlanStep(id="s1", description="搜索资料", expected="资料", depends_on=[])
    assert "用户的原始请求" not in _build_prompt(step, {}, "")


# ---------- 副作用登记（供单步重试时清理）----------

class _FakeSaveDownload(Tool):
    """冒充 save_download：产物 id 走 marker 字段（与真实 SaveDownloadTool 一致——
    marker 不进模型上下文，但会被 ToolExecutor 拼进 ToolResult.content）。"""
    name = "save_download"
    description = "保存下载"

    class Params(BaseModel):
        filename: str

    def __init__(self, did="d1", fail=False):
        self._did, self._fail = did, fail

    async def run(self, params):
        from harness.tools.base import ToolError
        if self._fail:
            raise ToolError(f"保存失败 〔下载ID:{self._did}〕")
        return ToolOutput(text="已保存", marker=f"〔下载ID:{self._did}〕")


def _reg_with_save_download(did="d1", fail=False):
    reg = ToolRegistry(); reg.register(_FakeSaveDownload(did, fail))
    return reg


def _save_download_turns():
    from harness.llm.base import ToolCallDelta
    return [
        [StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
            index=0, id="c1", name="save_download", arguments='{"filename": "a.md"}')),
         StreamChunk(type="done")],
        [StreamChunk(type="text", text="存好了"), StreamChunk(type="done")],
    ]


async def test_artifact_records_download_side_effect(make_mock):
    """带副作用的工具调完，产物 id 要挂在 StepArtifact 上——否则重试时无从知道该删什么。"""
    ex = Executor(client=make_mock(_save_download_turns()),
                  registry=_reg_with_save_download(), system_prompt="s", model="m", max_steps=3)
    _, artifact = await _collect(ex.execute(_step(), {}))
    assert artifact.side_effects["download"] == ["d1"]


async def test_artifact_records_nothing_when_tool_failed(make_mock):
    """工具报错时没有真产物，报错文本里回显的标记不能当成要清理的东西。"""
    ex = Executor(client=make_mock(_save_download_turns()),
                  registry=_reg_with_save_download(fail=True),
                  system_prompt="s", model="m", max_steps=3)
    _, artifact = await _collect(ex.execute(_step(), {}))
    assert artifact.side_effects["download"] == []


async def test_artifact_side_effects_empty_without_side_effect_tools(make_mock):
    from harness.tools.builtins.calculator import CalculatorTool
    reg = ToolRegistry(); reg.register(CalculatorTool())
    ex = Executor(client=make_mock(_tool_then_done_turns()),
                  registry=reg, system_prompt="s", model="m", max_steps=3)
    _, artifact = await _collect(ex.execute(_step(), {}))
    assert not any(artifact.side_effects.values())


class _BoomAfterSaveClient:
    """先让模型调一次 save_download，工具成功后下一轮流式炸掉——模拟网络中断。"""
    def __init__(self): self._n = 0

    async def stream(self, messages, schemas):
        from harness.llm.base import ToolCallDelta
        self._n += 1
        if self._n == 1:
            yield StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
                index=0, id="c1", name="save_download", arguments='{"filename": "a.md"}'))
            yield StreamChunk(type="done")
            return
        raise RuntimeError("连接断了")


async def test_side_effects_kept_when_step_errors_out():
    """工具已经落了盘、之后本步才出错 —— 产物 id 不能跟着错误一起丢，否则没人去删这个文件。

    AgentLoop 把客户端异常转成 RunError 而非抛出，所以这条路上 StepArtifact 仍会产出；
    清单同时写进 sink 与 artifact，两边都拿得到。
    """
    fx_sink = {}
    ex = Executor(client=_BoomAfterSaveClient(), registry=_reg_with_save_download(),
                  system_prompt="s", model="m", max_steps=3)
    _, artifact = await _collect(ex.execute(_step(), {}, fx_sink=fx_sink))
    assert artifact.error, "这轮应记为出错"
    assert fx_sink.get("download") == ["d1"]
    assert artifact.side_effects["download"] == ["d1"]


async def test_fx_sink_filled_on_normal_path_too(make_mock):
    """正常跑完时 sink 与 StepArtifact.side_effects 内容一致（调用方用哪个都对）。"""
    fx_sink = {}
    ex = Executor(client=make_mock(_save_download_turns()),
                  registry=_reg_with_save_download(), system_prompt="s", model="m", max_steps=3)
    _, artifact = await _collect(ex.execute(_step(), {}, fx_sink=fx_sink))
    assert fx_sink["download"] == ["d1"] == artifact.side_effects["download"]


# ---- effects：本次留下持久产物的工具，供重试时告知模型别重做 ----

async def _effects_of(make_mock, tool, tool_name):
    """跑一步「调一次该工具→出正文」，返回 StepArtifact.effects。"""
    from harness.llm.base import ToolCallDelta
    reg = ToolRegistry(); reg.register(tool)
    client = make_mock([
        [StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
            index=0, id="c1", name=tool_name, arguments="{}")),
         StreamChunk(type="done")],
        [StreamChunk(type="text", text="做完了"), StreamChunk(type="done")],
    ])
    ex = Executor(client=client, registry=reg, system_prompt="sp", model="m", max_steps=3)
    out = None
    async for ev in ex.execute(_step(), {}):
        if isinstance(ev, StepArtifact):
            out = ev
    return out.effects


class _NoParams(BaseModel):
    pass


class _MarkerTool(Tool):
    """按既有约定给产物发 id 的工具（如 save_download 的〔下载ID:x〕）。"""
    name = "saver"
    description = "存点东西"
    Params = _NoParams

    async def run(self, params):
        return ToolOutput(text="已保存", marker="〔下载ID:abc〕")


class _CrashSaver(Tool):
    """崩溃记账用：与 _MarkerTool 同形，单独一个类避免与别的用例共享状态。"""
    name = "saver"
    description = "存点东西"
    Params = _NoParams

    async def run(self, params):
        return ToolOutput(text="已保存", marker="〔下载ID:x〕")


class _PlainTool(Tool):
    name = "reader"
    description = "读点东西"
    Params = _NoParams

    async def run(self, params):
        return "读到了一些内容"


class _FailingMarkerTool(Tool):
    name = "saver"
    description = "存点东西"
    Params = _NoParams

    async def run(self, params):
        raise ToolError("保存失败：磁盘满")


async def test_effects_records_tool_that_left_a_product(make_mock):
    """判据是「结果带了 marker」——marker 是既有约定（〔下载ID〕〔知识ID〕），
    用它而非硬编码工具名单：名单会漂，marker 让新工具自动被覆盖。"""
    assert await _effects_of(make_mock, _MarkerTool(), "saver") == ["saver"]


async def test_effects_ignores_tools_without_a_product(make_mock):
    """检索类工具重跑无害，还进 effects 会让模型在「内容不足」的重试里不敢再搜。"""
    assert await _effects_of(make_mock, _PlainTool(), "reader") == []


async def test_effects_ignores_failed_calls(make_mock):
    """保存失败就没有产物，重试时当然该再存一次——报进去会让文件永远存不下来。"""
    assert await _effects_of(make_mock, _FailingMarkerTool(), "saver") == []


async def test_effects_accumulate_from_done_effects(make_mock):
    """本次没再调，不代表上次的产物消失了；下次重试仍须被告知。"""
    from harness.llm.base import ToolCallDelta   # noqa: F401  （本用例不调工具）
    reg = ToolRegistry()
    client = make_mock(_text_only_turns("这次只改文案，没调工具"))
    ex = Executor(client=client, registry=reg, system_prompt="sp", model="m", max_steps=3)
    out = None
    async for ev in ex.execute(_step(), {}, done_effects=["save_download"]):
        if isinstance(ev, StepArtifact):
            out = ev
    assert out.effects == ["save_download"]


async def test_crash_after_tool_still_accounts(make_mock):
    """子步崩在工具调用之后：产物已落库，调用方手里必须已经有账。"""
    from harness.llm.base import ToolCallDelta
    reg = ToolRegistry(); reg.register(_CrashSaver())
    client = make_mock([
        [StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
            index=0, id="c1", name="saver", arguments="{}")),
         StreamChunk(type="done")],
        [StreamChunk(type="text", text="继续"), StreamChunk(type="done")],
    ])
    ex = Executor(client=client, registry=reg, system_prompt="sp", model="m", max_steps=3)
    ledger = []
    gen = ex.execute(PlanStep(id="s1", description="d", expected="e"), {}, done_effects=ledger)
    # 模拟中途崩溃/中断：拿到工具完成事件后就不再迭代
    from harness.events import ToolFinished
    async for ev in gen:
        if isinstance(ev, ToolFinished):
            break
    await gen.aclose()
    assert ledger == ["saver"], f"崩溃路径没记账：{ledger}"


def test_orchestrator_only_passes_kwargs_that_executor_accepts():
    """编排器传给 execute() 的关键字参数，必须都是真实 Executor 认的。

    编排器测试里的 Executor 替身以 **_kw 收尾（两条工作线都在给 execute() 加参数，
    每个替身写死全部参数就会每次都改十几处、合并必冲突）。代价是替身不再因为签名对不上
    而报错——调用点打错字（done_effect=）或加了个 executor 没有的参数，1700 多个测试
    照样全绿。此处把那道交叉校验补回来：没有任何测试把真实 Executor 接进 Orchestrator。
    """
    import inspect
    import re

    from app.orchestration.executor import Executor
    from app.orchestration import orchestrator as orch_mod

    accepted = {
        n for n, p in inspect.signature(Executor.execute).parameters.items()
        if p.kind in (p.KEYWORD_ONLY, p.POSITIONAL_OR_KEYWORD)
    }
    src = inspect.getsource(orch_mod.Orchestrator._schedule_rounds)
    call = re.search(r"self\._executor\.execute\((.*?)\):", src, re.S)
    assert call, "没找到编排器对 execute() 的调用点，本测试需要跟着改"
    passed = set(re.findall(r"(\w+)\s*=", call.group(1)))

    unknown = passed - accepted
    assert not unknown, f"编排器传了 Executor 不认的参数：{unknown}"


async def test_executor_carries_reasoning_invisibility_guard():
    """回归：用户问「上一步思维链为什么是英文」，执行子步白调 search/recall/read_file
    全落空。根因是模型不知道思考内容不可检索。该指令放在 app_system_prompt（装配层把它
    作 Executor 基底，见 assembly.py:284），须确认它随基底流进子步发给模型的系统消息。"""
    from app.config import AppConfig
    seen = {}
    class ProbeClient:
        async def stream(self, messages, schemas):
            parts = [c for m in messages
                     if isinstance(c := (getattr(m, "content", None)
                                         or (m.get("content") if isinstance(m, dict) else None)), str)]
            seen["sys"] = "\n".join(parts)
            yield StreamChunk(type="text", text="ok"); yield StreamChunk(type="done")
    base = AppConfig(api_key="k", _env_file=None).app_system_prompt
    ex = Executor(client=ProbeClient(), registry=ToolRegistry(), system_prompt=base,
                  model="m", max_steps=1)
    await _collect(ex.execute(_step(), {}))
    assert "无法查看" in seen["sys"] and "思考过程" in seen["sys"]

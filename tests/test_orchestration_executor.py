from harness.events import Progress
from harness.llm.base import StreamChunk
from harness.tools.base import ToolRegistry
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
            # 便于测试直接查 artifact.error，而不必额外返回整个 StepArtifact 信号
            artifact.error = ev.error
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
    """docker 后端：如实报告基础容器/语言子沙箱的镜像与联网，并据网络说明能否装依赖。"""
    from app.sandbox_manager import sandbox_guide
    g = sandbox_guide(_sbx_cfg(
        sandbox_backend="docker", sandbox_image="quay.io/centos/centos:stream9",
        sandbox_network="bridge", sandbox_sub_network="none"))
    assert "quay.io/centos/centos:stream9" in g   # 基础容器镜像
    assert "python:3.12-slim" in g                # 语言子沙箱镜像（默认 lang_images）
    assert "可联网" in g and "禁止联网" in g        # base=bridge 可联网、子沙箱=none 禁网
    assert "pip install" in g                     # 联网环境可自行装包的指引


def test_sandbox_guide_docker_reflects_sub_network_online():
    """子沙箱放开网络（bridge）时，指引里子沙箱也应体现「可联网」。"""
    from app.sandbox_manager import sandbox_guide
    g = sandbox_guide(_sbx_cfg(
        sandbox_backend="docker", sandbox_network="none", sandbox_sub_network="bridge"))
    assert "可联网" in g and "禁止联网" in g        # 基础禁网、子沙箱可联网都如实出现


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

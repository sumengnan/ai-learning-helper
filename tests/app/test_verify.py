"""AnswerVerifier 单测——用假 complete / 假 registry，不打网络、不碰 Docker。"""
import json

import pytest

from app.config import AppConfig
from app.url_blocklist import UrlBlockedError
from app.verify import AnswerVerifier, TrajectoryJudge, _tool_exec_summary
from harness.tools.base import ToolError


def _cfg(**kw):
    return AppConfig(api_key="k", app_db_path=":memory:", _env_file=None, **kw)


def _fake_complete(mapping):
    """按 system_prompt 关键字返回预设 JSON 串。"""
    async def complete(system, user):
        for key, payload in mapping.items():
            if key in system:
                return json.dumps(payload, ensure_ascii=False)
        return "{}"
    return complete


# grounding 判 grounded=true / judge 高分：默认"全通过"的 complete
def _pass_complete():
    return _fake_complete({"事实核查": {"grounded": True, "feedback": ""},
                           "质检": {"score": 95, "feedback": "好"}})


async def test_format_empty_fails():
    v = AnswerVerifier(_pass_complete(), _cfg())
    verdict = await v.verify("问", "   ", [], None)
    assert verdict.ok is False and "format" in verdict.failed


async def test_format_unclosed_code_fence_fails():
    v = AnswerVerifier(_pass_complete(), _cfg())
    verdict = await v.verify("问", "看代码：\n```python\nprint(1)", [], None)
    assert verdict.ok is False and "format" in verdict.failed


async def test_all_pass_normal_answer():
    v = AnswerVerifier(_pass_complete(), _cfg(gate_check_code=False))
    verdict = await v.verify("光合作用是什么", "光合作用是植物把光能转化为化学能的过程。", [], None)
    assert verdict.ok is True and verdict.failed == []


async def test_grounding_fail_when_unsupported():
    complete = _fake_complete({"事实核查": {"grounded": False, "feedback": "X 无依据"},
                               "质检": {"score": 95, "feedback": ""}})
    v = AnswerVerifier(complete, _cfg(gate_check_code=False))
    grounding = [{"tool": "search_memory", "content": "[1]（来源：a）光合作用相关资料", "is_error": False}]
    verdict = await v.verify("问", "答案含臆造论断。", grounding, None)
    assert verdict.ok is False and "grounding" in verdict.failed
    assert "无依据" in verdict.critique


async def test_grounding_skipped_when_no_retrieval():
    # 本轮没有 search_memory 命中 → grounding 跳过（N/A 视为通过）
    complete = _fake_complete({"事实核查": {"grounded": False, "feedback": "不该被调用"},
                               "质检": {"score": 95, "feedback": ""}})
    v = AnswerVerifier(complete, _cfg(gate_check_code=False))
    verdict = await v.verify("问", "闲聊回答", [], None)
    assert verdict.ok is True and "grounding" not in verdict.failed


async def test_grounding_skipped_on_no_hit_sentinel():
    complete = _fake_complete({"事实核查": {"grounded": False, "feedback": "不该被调用"},
                               "质检": {"score": 95, "feedback": ""}})
    v = AnswerVerifier(complete, _cfg(gate_check_code=False))
    grounding = [{"tool": "search_memory", "content": "（未在知识库中检索到相关内容）", "is_error": False}]
    verdict = await v.verify("问", "答", grounding, None)
    assert verdict.ok is True


async def test_judge_low_score_fails():
    complete = _fake_complete({"事实核查": {"grounded": True, "feedback": ""},
                               "质检": {"score": 40, "feedback": "跑题"}})
    v = AnswerVerifier(complete, _cfg(gate_check_code=False, answer_pass_score=70))
    verdict = await v.verify("问", "答", [], None)
    assert verdict.ok is False and "judge" in verdict.failed and "跑题" in verdict.critique


class _StubCodeTool:
    """假代码工具：run 成功或按需抛 ToolError。"""
    class Params:
        def __init__(self, code, version=None):
            self.code = code

    def __init__(self, fail=False):
        self._fail = fail

    async def run(self, params):
        if self._fail:
            raise ToolError("exit_code=1\nstderr:\nSyntaxError")
        return "exit_code=0\nstdout:\nok"


class _StubRegistry:
    def __init__(self, tools):
        self._t = tools

    def get(self, name):
        return self._t.get(name)


async def test_code_block_error_fails():
    v = AnswerVerifier(_pass_complete(), _cfg(gate_check_grounding=False, gate_check_judge=False))
    reg = _StubRegistry({"run_python": _StubCodeTool(fail=True)})
    answer = "如下：\n```python\nprint(1+\n```"
    # 注意：上面围栏未闭合会先被 format 拦掉，这里用闭合的坏代码
    answer = "如下：\n```python\nprint( SyntaxError here\n```"
    verdict = await v.verify("写段代码", answer, [], reg)
    assert verdict.ok is False and "code" in verdict.failed
    assert "run_python" in verdict.critique


async def test_code_block_success_passes():
    v = AnswerVerifier(_pass_complete(), _cfg(gate_check_grounding=False, gate_check_judge=False))
    reg = _StubRegistry({"run_python": _StubCodeTool(fail=False)})
    answer = "如下：\n```python\nprint(1+1)\n```"
    verdict = await v.verify("写段代码", answer, [], reg)
    assert verdict.ok is True


async def test_code_block_with_ellipsis_skipped():
    # 含省略占位 → 非自包含 → 跳过执行（即便工具会失败也不判不过）
    v = AnswerVerifier(_pass_complete(), _cfg(gate_check_grounding=False, gate_check_judge=False))
    reg = _StubRegistry({"run_python": _StubCodeTool(fail=True)})
    answer = "示例：\n```python\ndef f():\n    ...\n```"
    verdict = await v.verify("写段代码", answer, [], reg)
    assert verdict.ok is True


async def test_llm_error_does_not_block_delivery():
    # judge 调用抛异常 → 该项跳过，不因基础设施抖动拦截交付
    async def boom(system, user):
        raise RuntimeError("LLM down")
    v = AnswerVerifier(boom, _cfg(gate_check_code=False, gate_check_grounding=False))
    verdict = await v.verify("问", "正常答案", [], None)
    assert verdict.ok is True


# ---- 硬门/软门 ----

async def test_format_is_hard_gate():
    v = AnswerVerifier(_pass_complete(), _cfg())
    verdict = await v.verify("问", "   ", [], None)
    assert "format" in verdict.hard_failed


async def test_judge_is_soft_gate():
    complete = _fake_complete({"事实核查": {"grounded": True}, "质检": {"score": 30, "feedback": "差"}})
    v = AnswerVerifier(complete, _cfg(gate_check_code=False))
    verdict = await v.verify("问", "答", [], None)
    assert "judge" in verdict.failed and verdict.hard_failed == []


# ---- grounding 逐句归因 ----

def test_grounding_prompt_excludes_greetings_and_advice():
    # 回归护栏：grounding 提示词须显式把「问候语/建议/鼓励/推理」排除在事实核查之外，
    # 否则核查模型会把它们误判为「缺依据的论断」导致交付被拦。
    from app.verify import GROUNDING_SYSTEM
    for kw in ("问候", "建议", "鼓励", "推理", "客观事实"):
        assert kw in GROUNDING_SYSTEM, f"grounding 提示词缺少排除项：{kw}"
    # 仍保留「事实核查」字样（既是职责说明，也是测试 mock 的匹配键）
    assert "事实核查" in GROUNDING_SYSTEM


async def test_grounding_unsupported_listed_in_critique():
    complete = _fake_complete({
        "事实核查": {"grounded": False, "unsupported": ["地球是平的", "水往高处流"], "feedback": ""},
        "质检": {"score": 95}})
    v = AnswerVerifier(complete, _cfg(gate_check_code=False))
    grounding = [{"tool": "search_memory", "content": "[1] 资料", "is_error": False}]
    verdict = await v.verify("问", "答", grounding, None)
    assert "grounding" in verdict.failed and "地球是平的" in verdict.critique


# ---- judge 独立模型 ----

async def test_judge_uses_independent_completer():
    main_c = _fake_complete({"事实核查": {"grounded": True}, "质检": {"score": 99}})
    judge_c = _fake_complete({"质检": {"score": 20, "feedback": "独立judge判差"}})
    v = AnswerVerifier(main_c, _cfg(gate_check_code=False, gate_check_grounding=False),
                       judge_complete=judge_c)
    verdict = await v.verify("问", "答", [], None)
    assert "judge" in verdict.failed and "独立judge" in verdict.critique


# ---- facts 引用链接可达性 ----

class _HttpStub:
    class Params:
        def __init__(self, url):
            self.url = url

    def __init__(self, body):
        self._body = body

    async def run(self, params):
        return self._body


async def test_facts_unreachable_link_fails():
    reg = _StubRegistry({"http_request": _HttpStub("HTTP 404\n标题：Not Found\n")})
    v = AnswerVerifier(_pass_complete(), _cfg(
        gate_check_grounding=False, gate_check_judge=False, gate_check_code=False,
        gate_check_facts=True))
    verdict = await v.verify("问", "详见 https://example.com/x 。", [], reg)
    assert "facts" in verdict.failed


async def test_facts_reachable_link_passes():
    reg = _StubRegistry({"http_request": _HttpStub("HTTP 200\n标题：OK\n")})
    v = AnswerVerifier(_pass_complete(), _cfg(
        gate_check_grounding=False, gate_check_judge=False, gate_check_code=False,
        gate_check_facts=True))
    verdict = await v.verify("问", "详见 https://example.com/x 。", [], reg)
    assert verdict.ok is True


class _BlockedHttpStub(_HttpStub):
    """命中失败登记 → 抓前短路（模拟 guard_fetch_tool 的行为）。"""
    async def run(self, params):
        raise UrlBlockedError(f"跳过抓取 {params.url}：近期抓取失败过",
                              {"reason": "HTTP 404（页面不存在）", "scope": "url"})


class _FlakyHttpStub(_HttpStub):
    async def run(self, params):
        raise TimeoutError("网络抖了一下")


async def test_facts_flags_link_known_dead_from_blocklist():
    # 登记过就是「我们知道它坏」的证据，不能当基建故障放行——否则加了失败登记反而
    # 让 facts 门对最确定的死链失明
    reg = _StubRegistry({"http_request": _BlockedHttpStub("")})
    v = AnswerVerifier(_pass_complete(), _cfg(
        gate_check_grounding=False, gate_check_judge=False, gate_check_code=False,
        gate_check_facts=True))
    verdict = await v.verify("问", "详见 https://example.com/x 。", [], reg)
    assert "facts" in verdict.failed
    assert "页面不存在" in verdict.critique


async def test_facts_still_passes_on_infra_flake():
    # 对比：普通抓取异常仍放行，不因基建抖动误拦回答
    reg = _StubRegistry({"http_request": _FlakyHttpStub("")})
    v = AnswerVerifier(_pass_complete(), _cfg(
        gate_check_grounding=False, gate_check_judge=False, gate_check_code=False,
        gate_check_facts=True))
    verdict = await v.verify("问", "详见 https://example.com/x 。", [], reg)
    assert verdict.ok is True


# ---- TrajectoryJudge ----

async def test_trajectory_judge_parses_scores():
    complete = _fake_complete({"过程质检": {"plan": 80, "steps": 70, "final": 90, "feedback": "不错"}})
    s = await TrajectoryJudge(complete, _cfg()).score("问", "拆分", "✓ search_memory", "答案")
    assert (s.plan, s.steps, s.final, s.feedback) == (80, 70, 90, "不错")


async def test_trajectory_judge_handles_null_scores():
    complete = _fake_complete({"过程质检": {"plan": None, "steps": None, "final": 85, "feedback": ""}})
    s = await TrajectoryJudge(complete, _cfg()).score("问", "", "", "答")
    assert s.plan is None and s.steps is None and s.final == 85


async def test_trajectory_judge_bad_json_degrades_to_none():
    async def boom(system, user):
        return "这不是JSON"
    s = await TrajectoryJudge(boom, _cfg()).score("问", "", "", "答")
    assert s.plan is None and s.steps is None and s.final is None


# ---- judge 工具执行上下文（避免工具型任务简短确认被误判低分）----

def test_tool_exec_summary_marks_success_and_failure():
    s = _tool_exec_summary([
        {"tool": "add_questions", "result": "已入库5道题", "is_error": False},
        {"tool": "run_python", "result": "报错", "is_error": True},
    ])
    assert "add_questions（成功）" in s and "已入库5道题" in s
    assert "run_python（失败）" in s


async def test_judge_receives_tool_summary():
    captured = {}
    async def cap(system, user):
        captured["user"] = user
        return json.dumps({"score": 90, "feedback": ""})
    v = AnswerVerifier(_pass_complete(),
                       _cfg(gate_check_grounding=False, gate_check_code=False),
                       judge_complete=cap)
    steps = [{"tool": "add_questions", "result": "已入库5道题", "is_error": False}]
    verdict = await v.verify("生成5道题保存到题库", "已完成", [], None, steps=steps)
    assert verdict.ok is True
    # judge 的输入里带上了工具执行摘要，才能公正评价「简短确认」
    assert "add_questions" in captured["user"] and "已入库5道题" in captured["user"]


# ---- structured output：judge/grounding LLM 调用强制 JSON ----

async def test_judge_call_forces_json_response_format():
    from harness.llm.openai_compat import get_extra_body_override
    seen = {}
    async def cap(system, user):
        seen["rf"] = get_extra_body_override().get("response_format")
        return json.dumps({"score": 90, "feedback": ""})
    v = AnswerVerifier(cap, _cfg(gate_check_grounding=False, gate_check_code=False))
    await v.verify("问", "答", [], None)
    assert seen["rf"] == {"type": "json_object"}   # judge 调用期间强制了 JSON 输出


def test_judge_prompt_allows_multiturn_clarification():
    # 回归护栏：judge 提示词须允许「针对无效/歧义输入的澄清、追问」，
    # 否则考试等多轮场景里 AI 对无效作答的正常提示会被误判为「未达成目标」。
    from app.verify import JUDGE_SYSTEM
    for kw in ("多轮", "无效", "澄清", "追问"):
        assert kw in JUDGE_SYSTEM, f"judge 提示词缺少多轮语境词：{kw}"

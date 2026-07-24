"""DeliveryChecker 单测——用假 complete / 假 registry，不打网络、不碰 Docker。

这些检查是**提醒型**的：产出 Notice 只意味着「值得扫一眼」，既不拦截交付、也不判本轮失败。
故断言的形状是「产生了哪几条提醒」，而不是旧交付门那种 ok/failed/hard_failed。
"""
import json

import pytest

from app.config import AppConfig
from app.url_blocklist import UrlBlockedError
from app.verify import DeliveryChecker, TrajectoryJudge, _tool_exec_summary
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


# grounding 判 grounded=true：默认"没有可提醒之处"的 complete
def _pass_complete():
    return _fake_complete({"事实核查": {"grounded": True, "feedback": ""}})


def _kinds(notices):
    return [n.kind for n in notices]


async def test_format_unclosed_code_fence_fails():
    v = DeliveryChecker(_pass_complete(), _cfg())
    notices = await v.run("看代码：\n```python\nprint(1)", [], None)
    assert _kinds(notices) == ["format"]


async def test_all_pass_normal_answer():
    v = DeliveryChecker(_pass_complete(), _cfg(delivery_check_code=False))
    notices = await v.run("光合作用是植物把光能转化为化学能的过程。", [], None)
    assert notices == []


async def test_grounding_fail_when_unsupported():
    complete = _fake_complete({"事实核查": {"grounded": False, "feedback": "X 无依据"},
                               "质检": {"score": 95, "feedback": ""}})
    v = DeliveryChecker(complete, _cfg(delivery_check_code=False))
    grounding = [{"tool": "search_knowledge", "content": "[1]（来源：a）光合作用相关资料", "is_error": False}]
    notices = await v.run("答案含臆造论断。", grounding, None)
    assert _kinds(notices) == ["grounding"]
    assert "无依据" in notices[0].text


async def test_grounding_skipped_when_no_retrieval():
    # 本轮没有 search_knowledge 命中 → grounding 跳过（N/A 视为通过）
    complete = _fake_complete({"事实核查": {"grounded": False, "feedback": "不该被调用"},
                               "质检": {"score": 95, "feedback": ""}})
    v = DeliveryChecker(complete, _cfg(delivery_check_code=False))
    notices = await v.run("闲聊回答", [], None)
    assert _kinds(notices) == []


async def test_grounding_includes_web_retrieval_context():
    """核心修复：联网检索的结果也是「检索依据」，须一并交给核查模型。
    否则「知识库+联网」混用时，联网来的事实会因不在知识库而被误判缺依据。"""
    seen = {}

    async def complete(system, user):
        if "事实核查" in system:
            seen["context"] = user            # 记下核查模型实际看到的资料
            return json.dumps({"grounded": True, "feedback": ""})
        return json.dumps({"score": 95, "feedback": ""})

    v = DeliveryChecker(complete, _cfg(delivery_check_code=False))
    grounding = [
        {"tool": "search_knowledge", "content": "知识库：光合作用发生在叶绿体",
         "is_error": False, "retrieval": True},
        {"tool": "mcp__websearch__bailian_web_search",
         "content": "联网：2026 年 Spring AI 发布 2.0", "is_error": False, "retrieval": True},
    ]
    notices = await v.run("光合作用在叶绿体；Spring AI 2026 出了 2.0。", grounding, None)
    assert notices == []                      # 两条论断各有依据 → 不该提醒
    assert "叶绿体" in seen["context"] and "Spring AI" in seen["context"]  # 两类来源都进了核查上下文


async def test_grounding_includes_read_document_context():
    """整理笔记场景：read_attachment/read_file 读入的文档正文也须作为核查资料，
    否则「把知识库整理成笔记」会因内容不在本轮 top-k 检索片段里而被误判缺依据。"""
    seen = {}

    async def complete(system, user):
        if "事实核查" in system:
            seen["context"] = user
            return json.dumps({"grounded": True, "feedback": ""})
        return json.dumps({"score": 95, "feedback": ""})

    v = DeliveryChecker(complete, _cfg(delivery_check_code=False))
    grounding = [
        {"tool": "search_knowledge", "content": "知识库片段：光合作用", "is_error": False, "retrieval": True},
        {"tool": "read_attachment", "content": "文档正文：叶绿体是光合作用的场所", "is_error": False},
    ]
    notices = await v.run("整理的笔记内容。", grounding, None)
    assert notices == []
    assert "叶绿体是光合作用的场所" in seen["context"]   # 读入的文档进了核查上下文


async def test_grounding_not_expanded_to_web_only():
    """纯联网轮（无知识库命中）不触发 grounding —— 刻意不扩大触发面，避免给大量联网
    问答新增 grounding 噪音。本次只修「知识库+联网混用时联网内容被误判」，不改触发条件。"""
    complete = _fake_complete({"事实核查": {"grounded": False, "feedback": "不该被调用"},
                               "质检": {"score": 95, "feedback": ""}})
    v = DeliveryChecker(complete, _cfg(delivery_check_code=False))
    grounding = [{"tool": "browse", "content": "网页内容：只讲了 A", "is_error": False,
                  "retrieval": True}]
    notices = await v.run("答案含 B 这条论断。", grounding, None)
    assert _kinds(notices) == []                      # 无知识库锚点 → 跳过


async def test_grounding_skipped_on_no_hit_sentinel():
    complete = _fake_complete({"事实核查": {"grounded": False, "feedback": "不该被调用"},
                               "质检": {"score": 95, "feedback": ""}})
    v = DeliveryChecker(complete, _cfg(delivery_check_code=False))
    grounding = [{"tool": "search_knowledge", "content": "（未在知识库中检索到相关内容）", "is_error": False}]
    notices = await v.run("答", grounding, None)
    assert notices == []


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
    v = DeliveryChecker(_pass_complete(), _cfg(delivery_check_grounding=False))
    reg = _StubRegistry({"run_python": _StubCodeTool(fail=True)})
    answer = "如下：\n```python\nprint(1+\n```"
    # 注意：上面围栏未闭合会先被 format 拦掉，这里用闭合的坏代码
    answer = "如下：\n```python\nprint( SyntaxError here\n```"
    notices = await v.run(answer, [], reg)
    assert _kinds(notices) == ["code"]
    assert "run_python" in notices[0].text


async def test_code_block_success_passes():
    v = DeliveryChecker(_pass_complete(), _cfg(delivery_check_grounding=False))
    reg = _StubRegistry({"run_python": _StubCodeTool(fail=False)})
    answer = "如下：\n```python\nprint(1+1)\n```"
    notices = await v.run(answer, [], reg)
    assert notices == []


async def test_code_block_with_ellipsis_skipped():
    # 含省略占位 → 非自包含 → 跳过执行（即便工具会失败也不判不过）
    v = DeliveryChecker(_pass_complete(), _cfg(delivery_check_grounding=False))
    reg = _StubRegistry({"run_python": _StubCodeTool(fail=True)})
    answer = "示例：\n```python\ndef f():\n    ...\n```"
    notices = await v.run(answer, [], reg)
    assert notices == []


async def test_llm_error_does_not_block_delivery():
    # judge 调用抛异常 → 该项跳过，不因基础设施抖动拦截交付
    async def boom(system, user):
        raise RuntimeError("LLM down")
    v = DeliveryChecker(boom, _cfg(delivery_check_code=False, delivery_check_grounding=False))
    notices = await v.run("正常答案", [], None)
    assert notices == []


# ---- 硬门/软门 ----


def test_grounding_prompt_excludes_greetings_and_advice():
    # 回归护栏：grounding 提示词须显式把「问候语/建议/鼓励/推理」排除在事实核查之外，
    # 否则核查模型会把它们误判为「缺依据的论断」导致交付被拦。
    from app.verify import GROUNDING_SYSTEM
    for kw in ("问候", "建议", "鼓励", "推理", "客观事实", "改写", "笔记"):
        assert kw in GROUNDING_SYSTEM, f"grounding 提示词缺少排除项：{kw}"
    # 仍保留「事实核查」字样（既是职责说明，也是测试 mock 的匹配键）
    assert "事实核查" in GROUNDING_SYSTEM


async def test_grounding_unsupported_listed_in_critique():
    complete = _fake_complete({
        "事实核查": {"grounded": False, "unsupported": ["地球是平的", "水往高处流"], "feedback": ""},
        "质检": {"score": 95}})
    v = DeliveryChecker(complete, _cfg(delivery_check_code=False))
    grounding = [{"tool": "search_knowledge", "content": "[1] 资料", "is_error": False}]
    notices = await v.run("答", grounding, None)
    assert _kinds(notices) == ["grounding"] and "地球是平的" in notices[0].text


# ---- judge 独立模型 ----


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
    v = DeliveryChecker(_pass_complete(), _cfg(
        delivery_check_grounding=False, delivery_check_code=False,
        delivery_check_facts=True))
    notices = await v.run("详见 https://example.com/x 。", [], reg)
    assert _kinds(notices) == ["facts"]


async def test_facts_reachable_link_passes():
    reg = _StubRegistry({"http_request": _HttpStub("HTTP 200\n标题：OK\n")})
    v = DeliveryChecker(_pass_complete(), _cfg(
        delivery_check_grounding=False, delivery_check_code=False,
        delivery_check_facts=True))
    notices = await v.run("详见 https://example.com/x 。", [], reg)
    assert notices == []


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
    v = DeliveryChecker(_pass_complete(), _cfg(
        delivery_check_grounding=False, delivery_check_code=False,
        delivery_check_facts=True))
    notices = await v.run("详见 https://example.com/x 。", [], reg)
    assert _kinds(notices) == ["facts"] and "页面不存在" in notices[0].text


async def test_facts_still_passes_on_infra_flake():
    # 对比：普通抓取异常仍放行，不因基建抖动误拦回答
    reg = _StubRegistry({"http_request": _FlakyHttpStub("")})
    v = DeliveryChecker(_pass_complete(), _cfg(
        delivery_check_grounding=False, delivery_check_code=False,
        delivery_check_facts=True))
    notices = await v.run("详见 https://example.com/x 。", [], reg)
    assert notices == []


# ---- TrajectoryJudge ----

async def test_trajectory_judge_parses_scores():
    complete = _fake_complete({"过程质检": {"plan": 80, "steps": 70, "final": 90, "feedback": "不错"}})
    s = await TrajectoryJudge(complete, _cfg()).score("问", "拆分", "✓ search_knowledge", "答案")
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


def test_trajectory_prompt_forbids_leaking_schema_into_feedback():
    """feedback 是直接展示给用户的，不该出现字段名/null——曾出现「拆分字段为null，
    步骤和最终答案均高质有效」这种评语：judge 在讲自己的 JSON，不是在评价回答。"""
    from app.verify import TRAJECTORY_SYSTEM
    assert "直接展示给用户看" in TRAJECTORY_SYSTEM
    assert "不要提 null" in TRAJECTORY_SYSTEM
    assert "字段名" in TRAJECTORY_SYSTEM
    # 保留：无拆分/无步骤时该字段仍须给 null（前端据此不显示该项，而非显示 0 分）
    assert "对应字段给 null" in TRAJECTORY_SYSTEM


async def test_null_plan_passes_through_as_none():
    # 模型没调 plan 工具 → 无拆分可评 → plan 为 null，不能被强转成 0
    async def _judge(system, user):
        return '{"plan": null, "steps": 100, "final": 100, "feedback": "完成得不错"}'
    j = TrajectoryJudge(_judge, _cfg())
    s = await j.score("问", "", "步骤摘要", "答案")
    assert s.plan is None and s.steps == 100 and s.final == 100


# ── 提醒型语义（这次改动的要害）─────────────────────────────────────────────────
async def test_notices_are_advisory_not_a_verdict():
    """Notice 上没有任何「成败」字段：它不参与本轮是否成功的判断。

    这条钉住的是设计意图。旧交付门返回 Verdict(ok/failed/hard_failed)，调用方据此
    重答甚至拦截交付；现在只返回提醒，谁都不能拿它当判定。
    """
    v = DeliveryChecker(_pass_complete(), _cfg())
    notices = await v.run("看代码：\n```python\nprint(1)", [], None)
    n = notices[0]
    assert not hasattr(n, "ok") and not hasattr(n, "failed")
    assert n.as_dict() == {"kind": "format", "label": "完整性", "text": n.text}


async def test_empty_answer_produces_no_notice():
    """空产出交由上游的错误处理表态，检查器不凑热闹再补一条提醒。"""
    v = DeliveryChecker(_pass_complete(), _cfg())
    assert await v.run("   ", [], None) == []


async def test_all_items_run_no_short_circuit():
    """各项独立跑完，不像旧交付门那样在 format 处短路——既然不拦截，就该把问题一次报全。"""
    complete = _fake_complete({"事实核查": {"grounded": False, "feedback": "X 无依据"}})
    reg = _StubRegistry({"run_python": _StubCodeTool(fail=True)})
    grounding = [{"tool": "search_knowledge", "content": "资料", "is_error": False}]
    v = DeliveryChecker(complete, _cfg())
    # 截断 + 缺依据 + 代码跑不通，三项同时成立
    notices = await v.run("见代码：\n```python\nprint(1)\n```\n还有未闭合的：\n```python\nx",
                          grounding, reg)
    assert set(_kinds(notices)) == {"format", "grounding", "code"}


async def test_infra_flake_produces_no_false_notice():
    """基建抖动一律静默跳过：假提醒比不提醒更糟，它会训练用户忽略所有提醒。"""
    async def boom(system, user):
        raise RuntimeError("端点抖了")
    grounding = [{"tool": "search_knowledge", "content": "资料", "is_error": False}]
    v = DeliveryChecker(boom, _cfg(delivery_check_code=False))
    assert await v.run("正常答案", grounding, None) == []


async def test_each_item_can_be_switched_off():
    reg = _StubRegistry({"run_python": _StubCodeTool(fail=True)})
    v = DeliveryChecker(_pass_complete(), _cfg(delivery_check_code=False,
                                               delivery_check_format=False))
    notices = await v.run("```python\nprint(1)\n```\n未闭合：\n```python\nx", [], reg)
    assert notices == []

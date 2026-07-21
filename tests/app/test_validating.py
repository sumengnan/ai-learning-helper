"""ValidatingTool 每步校验单测——不打网络/Docker，用假工具 + 捕获 emit。"""
import pytest
from pydantic import BaseModel

from harness.tools.base import Tool, ToolError
from harness.types import ToolOutput
from harness.progress import set_emitter, reset_emitter

from app.tools.validating import ValidatingTool, relevance_check, NO_HIT_MARK


class _P(BaseModel):
    query: str = ""


class _SearchStub(Tool):
    name = "search_knowledge"
    description = "d"
    Params = _P

    def __init__(self, ret):
        self._ret = ret

    async def run(self, params):
        return self._ret


class _ExecStub(Tool):
    name = "run_python"
    description = "d"
    Params = _P

    def __init__(self, fail=False):
        self._fail = fail

    async def run(self, params):
        if self._fail:
            raise ToolError("exit_code=1\nSyntaxError")
        return "exit_code=0\nok"


def _capture():
    evs = []
    return evs, set_emitter(evs.append)


# ---- 纯规则 ----

def test_no_hit_is_not_a_failure_but_still_hints():
    """知识库空命中是合法结果，不标红——但仍要提醒模型别据此臆造。

    判失败会给用户留一个消不掉的红标，且模型无法靠重试纠正（换关键词也变不出没存过
    的文档）。与记忆检索同理：那边压根没包校验。
    """
    r = relevance_check(NO_HIT_MARK)
    assert r.ok is True
    assert r.hint, "不标红，但提醒必须还在——否则模型会拿空结果硬编"


def test_empty_return_is_still_a_failure():
    """空命中有哨兵文案；连哨兵都没有 = 检索没正常工作，这才是真失败。"""
    assert relevance_check("").ok is False
    assert relevance_check("   ").ok is False


def test_relevance_check_hit():
    r = relevance_check("[1]（来源：a）命中资料")
    assert r.ok is True and r.hint == ""


# ---- result 型（检索）----

async def test_search_no_hit_appends_hint_without_marking_error():
    """回归：空命中曾被标成 error，用户每轮都看到「未命中知识库」一直报错。
    提示要留（模型需要知道别臆造），红标要去（不是失败，重试也没用）。"""
    evs, tok = _capture()
    try:
        out = await ValidatingTool(_SearchStub(NO_HIT_MARK), relevance_check).run(_P())
    finally:
        reset_emitter(tok)
    assert "[校验提示]" in out                                   # hint 仍追加
    checks = [e for e in evs if e.scope == "check"]
    assert checks and not any(e.status == "error" for e in checks)


async def test_search_hit_returns_verbatim_and_emits_ok():
    evs, tok = _capture()
    try:
        out = await ValidatingTool(_SearchStub("[1] 命中资料"), relevance_check).run(_P())
    finally:
        reset_emitter(tok)
    assert out == "[1] 命中资料"                                 # 原样返回、不加料
    assert any(e.scope == "check" and e.status == "ok" for e in evs)


async def test_tooloutput_hint_preserves_shape():
    evs, tok = _capture()
    try:
        inner = _SearchStub(ToolOutput(text=NO_HIT_MARK, follow_up=["x"]))
        out = await ValidatingTool(inner, relevance_check).run(_P())
    finally:
        reset_emitter(tok)
    assert isinstance(out, ToolOutput) and "[校验提示]" in out.text and out.follow_up == ["x"]


async def test_check_exception_does_not_swallow_result():
    def boom(_text):
        raise ValueError("校验器炸了")
    evs, tok = _capture()
    try:
        out = await ValidatingTool(_SearchStub("原始结果"), boom).run(_P())
    finally:
        reset_emitter(tok)
    assert out == "原始结果"                                     # 放行：绝不吞原结果


# ---- exec 型（代码/命令）----

async def test_exec_pass_emits_ok():
    evs, tok = _capture()
    try:
        out = await ValidatingTool(_ExecStub(fail=False), exec_mode=True).run(_P())
    finally:
        reset_emitter(tok)
    assert out == "exit_code=0\nok"
    assert any(e.scope == "check" and e.status == "ok" for e in evs)


async def test_exec_fail_emits_error_and_reraises():
    evs, tok = _capture()
    try:
        with pytest.raises(ToolError):
            await ValidatingTool(_ExecStub(fail=True), exec_mode=True).run(_P())
    finally:
        reset_emitter(tok)
    assert any(e.scope == "check" and e.status == "error" for e in evs)


# ---- 透明代理 ----

def test_transparent_name_params_schema():
    inner = _SearchStub("x")
    t = ValidatingTool(inner, relevance_check)
    assert t.name == "search_knowledge"
    assert t.Params is _P
    assert t.schema() == inner.schema()


# ---------- 联网抓取校验：抓取成功 ≠ 抓到了能当依据的东西 ----------

from app.tools.validating import web_content_check   # noqa: E402

# 用户实际遇到的那次：模型编了个不存在的网址，example.com 真实存在且恒返回 200，
# 于是状态码、错误页、有无正文三道判据全部放行，结果被记成参考来源 [3]。
_EXAMPLE_COM_RESULT = (
    "标题：Example Domain\n"
    "最终URL：https://example.com/ai-agent-advancements\n\n"
    "This domain is for use in documentation examples without needing permission. "
    "Avoid use in operations.\nLearn more")


def test_web_check_rejects_placeholder_domain():
    r = web_content_check(_EXAMPLE_COM_RESULT)
    assert r.ok is False
    assert "占位域名" in r.text
    assert "编造" in r.hint          # 提示要点明「网址可能是编的」，驱动模型改用搜索


def test_web_check_rejects_parked_domain_by_text():
    r = web_content_check("标题：x\n最终URL：https://real-looking-site.com/a\n\n"
                          "This domain is for sale. Buy this domain now." + "x" * 200)
    assert r.ok is False


def test_web_check_rejects_near_empty_body():
    r = web_content_check("HTTP 200\n标题：某页\n最终URL：https://a.com/x\n\n短")
    assert r.ok is False and "过短" in r.text


def test_web_check_passes_real_page():
    body = "光合作用是植物利用光能把二氧化碳和水转化为葡萄糖和氧气的过程。" * 5
    r = web_content_check(f"HTTP 200\n标题：光合作用\n最终URL：https://zh.wikipedia.org/wiki/x\n\n{body}")
    assert r.ok is True


def test_web_check_ignores_non_page_results():
    """JSON/API 原样透传的结果没有「最终URL：」行，形态不可预期、短也正常 → 不判，免误伤。"""
    assert web_content_check('HTTP 200\n{"ok":true}').ok is True
    assert web_content_check("HTTP 200\n[]").ok is True


def test_app_ships_a_measured_relevance_floor(monkeypatch):
    """应用层默认必须开着相关性下限。

    内核默认 0（模型无关，不替精排模型断言量纲），但应用层推荐了具体精排模型，
    实测值就该落在这层。若默认改回 0，「查厨具也能从 AI 知识库返回满满一屏」这个
    bug 会原样复活，且没有任何测试会红——故在此钉死。
    换精排模型时请重新实测再改这个数，同时更新 .env.example 里的标定说明。

    必须与本机配置隔离（_env_file=None + 清掉环境变量）：这里断言的是**代码里的
    默认值**，而开发者在自己 .env 里配一个 HARNESS_RERANK_MIN_SCORE 是完全正常的事。
    不隔离的话本机一配就红，且红的原因与它要守护的不变式毫无关系——一条常年飘红、
    每次都要解释"这个不用管"的测试，等于没有这条测试。
    """
    from app.config import AppConfig
    from harness.config import HarnessConfig
    monkeypatch.delenv("HARNESS_RERANK_MIN_SCORE", raising=False)
    assert HarnessConfig(_env_file=None).rerank_min_score == 0.0, "内核保持模型无关"
    assert AppConfig(api_key="k", _env_file=None).rerank_min_score > 0, "应用层必须带实测下限"

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
    name = "search_memory"
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

def test_relevance_check_no_hit_and_empty():
    assert relevance_check(NO_HIT_MARK).ok is False
    assert relevance_check("").ok is False
    assert relevance_check("   ").ok is False


def test_relevance_check_hit():
    r = relevance_check("[1]（来源：a）命中资料")
    assert r.ok is True and r.hint == ""


# ---- result 型（检索）----

async def test_search_no_hit_appends_hint_and_emits_error():
    evs, tok = _capture()
    try:
        out = await ValidatingTool(_SearchStub(NO_HIT_MARK), relevance_check).run(_P())
    finally:
        reset_emitter(tok)
    assert "[校验提示]" in out                                   # hint 追加，驱动自纠正
    assert any(e.scope == "check" and e.status == "error" for e in evs)


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
    assert t.name == "search_memory"
    assert t.Params is _P
    assert t.schema() == inner.schema()

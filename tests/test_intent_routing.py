"""意图路由：triage 顺带产出类别 → 本轮面向用户的调用按类别选温度。

要点是**不额外多一次模型调用**：类别搭在每轮都跑的那次 triage 上，故解析必须能容忍
模型不照格式回答——认不出一律回退 chat（= 项目原本的 0.7），误分类的代价有界。
"""
import pytest

from app.orchestration.orchestrator import Orchestrator
from app.sampling_policy import INTENT_FALLBACK


def _orch(reply, calls=None):
    """只装 triage 需要的那一个依赖（run() 的其余部分不参与本文件）。"""
    o = Orchestrator.__new__(Orchestrator)

    async def _fast(system, user):
        if calls is not None:
            calls.append(user)
        if isinstance(reply, Exception):
            raise reply
        return reply

    o._fast_complete = _fast
    return o


@pytest.mark.parametrize("raw,simple,intent", [
    ("simple:factual", True, "factual"),
    ("complex:code", False, "code"),
    ("simple:chat", True, "chat"),
    ("SIMPLE:CREATIVE", True, "creative"),          # 大小写不敏感
    ("complex：rewrite", False, "rewrite"),          # 中文冒号
    ("simple: exam", True, "exam"),                 # 冒号后有空格
])
async def test_parses_both_fields(raw, simple, intent):
    assert await _orch(raw)._triage("m") == (simple, intent)


@pytest.mark.parametrize("raw", [
    "simple",                    # 老格式：只有分流词，没有类别
    "simple:",                   # 类别为空
    "simple:musical",            # 表外的词
    "simple:factual 因为用户在问事实",   # 后面拖了解释——取第一个词
])
async def test_intent_falls_back_but_routing_still_works(raw):
    simple, intent = await _orch(raw)._triage("m")
    assert simple is True
    if raw.startswith("simple:factual"):
        assert intent == "factual"
    else:
        assert intent == INTENT_FALLBACK


async def test_triage_failure_is_conservative():
    """判不了 → 走完整编排（宁可多做不可少做）+ 用回退温度，不猜。"""
    assert await _orch(RuntimeError("端点抖了"))._triage("m") == (False, INTENT_FALLBACK)


async def test_recent_dialogue_is_fed_to_triage():
    """追问（「再详细点」）只看孤立一句会被判简单，故最近对话必须带上。"""
    calls = []
    await _orch("complex:code", calls)._triage("再详细点", recent_dialogue="上文" * 10)
    assert "最近对话" in calls[0] and "本次消息" in calls[0]


async def test_is_simple_still_works_as_a_thin_wrapper():
    """老接口保留：只要分流结论的调用方（含既有测试）不必知道意图这回事。"""
    assert await _orch("simple:factual")._is_simple("m") is True
    assert await _orch("complex:code")._is_simple("m") is False


# ── run() 期间真的按意图设了温度，且离开时还原 ──────────────────────────────────
def _runnable(intent, temp_by_intent=None):
    """够跑 run() 简单直答那条路的最小编排器。"""
    from harness.events import RunFinished
    from harness.types import Message, Role

    o = Orchestrator.__new__(Orchestrator)

    async def _triage(msg, recent_dialogue=""):
        return True, intent

    seen = {}

    async def _simple(msg, budget=None, *, context=None, registry=None,
                      prefer_main=False, skill_hint=""):
        from harness.llm.sampling import resolve_sampling
        seen["temp"] = resolve_sampling(0.7)["temperature"]
        yield RunFinished(message=Message(role=Role.ASSISTANT, content="答复"))

    o._triage = _triage
    o._simple_answer = _simple
    o._budget = o._budget_factory = None
    o._intent_temperature = (temp_by_intent or {"factual": 0.2, "creative": 0.9}).get
    return o, seen


async def test_intent_temperature_applies_during_the_run():
    o, seen = _runnable("factual")
    _ = [ev async for ev in o.run("这个报错是什么意思", verify=False)]
    assert seen["temp"] == 0.2


async def test_creative_intent_gets_a_hotter_run():
    o, seen = _runnable("creative")
    _ = [ev async for ev in o.run("给我的猫起十个名字", verify=False)]
    assert seen["temp"] == 0.9


async def test_intent_temperature_is_reset_after_the_run():
    """run() 有十几个 return 出口，漏还原会把这轮的温度带给后面所有调用（含判分）。"""
    from harness.llm.sampling import resolve_sampling
    o, _ = _runnable("creative")
    _ = [ev async for ev in o.run("起名字", verify=False)]
    assert resolve_sampling(0.7)["temperature"] == 0.7


async def test_exam_turn_uses_exam_intent_without_calling_triage():
    """考试轮强制单循环，本就不问 triage——温度也该按 exam 档直接给。"""
    calls = []
    o, seen = _runnable("factual", {"exam": 0.2})

    async def _spy(msg, recent_dialogue=""):
        calls.append(msg)
        return True, "creative"

    o._triage = _spy
    _ = [ev async for ev in o.run("下一题", verify=False, force_simple=True)]
    assert calls == [] and seen["temp"] == 0.2

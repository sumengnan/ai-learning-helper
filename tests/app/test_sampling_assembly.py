"""装配层接线：温度策略确实接到了各调用点，且配置开关真的能关掉动态部分。

接线测试不可省——把 with_role 从某个调用点摘掉，别处没有任何测试会红。
"""
import pytest

from app.assembly import build_harness
from app.completion import build_completer, with_role
from app.config import AppConfig
from harness.llm.sampling import resolve_sampling


def _cfg(**kw):
    return AppConfig(api_key="sk-test", app_db_path=":memory:", **kw)


# ── with_role 本身 ─────────────────────────────────────────────────────────────
async def test_with_role_sets_and_restores_temperature():
    seen = {}

    async def base(system, user):
        seen["temp"] = resolve_sampling(0.7)["temperature"]
        return "ok"

    wrapped = with_role(base, _cfg(), "judge")
    assert await wrapped("s", "u") == "ok"
    assert seen["temp"] == 0.0                                # 判分档
    assert resolve_sampling(0.7)["temperature"] == 0.7        # 调完还原


async def test_with_role_is_a_noop_for_unknown_role():
    async def base(system, user):
        return "ok"

    assert with_role(base, _cfg(), "查无此角色") is base       # 原样返回，零开销


async def test_with_role_respects_config_override():
    seen = {}

    async def base(system, user):
        seen["temp"] = resolve_sampling(0.7)["temperature"]
        return ""

    await with_role(base, _cfg(role_temperatures={"judge": 0.5}), "judge")("s", "u")
    assert seen["temp"] == 0.5


# ── 编排器接线 ─────────────────────────────────────────────────────────────────
def test_orchestrator_gets_intent_and_dynamic_wiring():
    o = build_harness(_cfg()).orchestrator
    assert o._intent_temperature("factual") == 0.2
    assert o._intent_temperature("creative") == 0.9
    assert o._intent_temperature("没这个意图") == 0.7          # 回退，不赌
    assert o._dynamic_temperature is True
    assert o._planner._dynamic_temperature is True
    assert o._executor._temperature == 0.2                    # 执行子步：低但不为 0


def test_dynamic_temperature_can_be_switched_off():
    """关掉后只剩静态分档：重试/纠偏不再动温度。"""
    o = build_harness(_cfg(enable_dynamic_temperature=False)).orchestrator
    assert o._dynamic_temperature is False
    assert o._planner._dynamic_temperature is False
    assert o._intent_temperature is not None                  # 静态意图档不受影响


def test_role_override_reaches_the_orchestrator():
    o = build_harness(_cfg(role_temperatures={"executor": 0.4})).orchestrator
    assert o._executor._temperature == 0.4


def test_intent_override_reaches_the_orchestrator():
    o = build_harness(_cfg(intent_temperatures={"creative": 0.6})).orchestrator
    assert o._intent_temperature("creative") == 0.6


@pytest.mark.parametrize("bad,want", [({"executor": 5}, 1.0), ({"executor": -2}, 0.0)])
def test_out_of_range_override_is_clamped_not_rejected(bad, want):
    assert build_harness(_cfg(role_temperatures=bad)).orchestrator._executor._temperature == want


# ── 各角色确实各走各的档（抽查三处最易漂移的）────────────────────────────────────
async def test_quiz_grade_and_generate_use_different_temperatures():
    """同一个 completer 两种采样：判分要可复现，出题要不重样。"""
    seen = []

    async def base(system, user):
        seen.append(resolve_sampling(0.7)["temperature"])
        return ""

    cfg = _cfg()
    await with_role(base, cfg, "quiz_grade")("s", "u")
    await with_role(base, cfg, "quiz_generate")("s", "u")
    assert seen[0] == 0.0 and seen[1] >= 0.7


async def test_memory_reconcile_is_deterministic():
    """判 REPLACE 会永久作废旧记忆，最忌抖动。"""
    seen = {}

    async def base(system, user):
        seen["t"] = resolve_sampling(0.7)["temperature"]
        return ""

    await with_role(base, _cfg(), "memory_reconcile")("s", "u")
    assert seen["t"] == 0.0


async def test_build_completer_alone_does_not_pin_temperature():
    """没挂角色的 completer 保持基准值——本次改动不该悄悄改动未涉及的调用点。"""
    seen = {}

    class _Probe:
        async def stream(self, messages, tools):
            seen["t"] = resolve_sampling(0.7)["temperature"]
            return
            yield   # pragma: no cover

    await build_completer(_Probe(), "m")("s", "u")
    assert seen["t"] == 0.7

"""重试期的升/降温：方向、档位、开关、还原。

方向是这套东西的要害：陷在同一条路上（打转、重答、单步重试）要升温换采样，
吐出非法结构（planner 出坏 DAG）要降温收紧。搞反了两边都更糟。
"""
import pytest

from app.api.chat import _redo_temperature
from app.orchestration.orchestrator import _step_retry_temperature
from app.orchestration.planner import Planner
from harness.llm.sampling import resolve_sampling, sampling


class _Cfg:
    def __init__(self, on=True):
        self.enable_dynamic_temperature = on


def _temp():
    return resolve_sampling(0.7)["temperature"]


# ── 整轮重答（交付门未过）──────────────────────────────────────────────────────
def test_redo_first_attempt_does_not_change_temperature():
    with _redo_temperature(_Cfg(), 0):
        assert _temp() == 0.7


def test_redo_raises_temperature_on_later_attempts():
    with _redo_temperature(_Cfg(), 1):
        assert _temp() == pytest.approx(0.85)
    with _redo_temperature(_Cfg(), 2):
        assert _temp() == 1.0            # 0.7+0.3，且被上限夹住


def test_redo_stacks_on_the_intent_temperature():
    """重答升的是「本轮意图定下的那个温度」，不是凭空的绝对值。"""
    with sampling(temperature=0.2), _redo_temperature(_Cfg(), 1):
        assert _temp() == pytest.approx(0.35)


def test_redo_restores_after_the_attempt():
    with _redo_temperature(_Cfg(), 2):
        pass
    assert _temp() == 0.7


def test_redo_is_a_noop_when_switched_off():
    with _redo_temperature(_Cfg(on=False), 2):
        assert _temp() == 0.7


# ── 单步重试 ───────────────────────────────────────────────────────────────────
def test_step_retry_raises_temperature():
    with _step_retry_temperature(True, 1):
        assert _temp() == pytest.approx(0.85)


def test_step_retry_noop_on_first_attempt_and_when_off():
    with _step_retry_temperature(True, 0):
        assert _temp() == 0.7
    with _step_retry_temperature(False, 3):
        assert _temp() == 0.7


# ── 规划重试：方向相反，是降温 ─────────────────────────────────────────────────
async def test_planner_lowers_temperature_on_each_retry():
    """坏 DAG 说明采样太散，拿同一个温度再吐一遍没有意义。"""
    seen = []
    bad = '{"steps": [{"id": "s1"}]}'          # 缺字段 → _parse_steps 失败
    good = ('{"steps": [{"id": "s1", "description": "做事", "expected": "一份结果",'
            ' "depends_on": []}]}')

    async def complete(system, user):
        seen.append(_temp())
        return good if len(seen) >= 3 else bad

    await Planner(complete, max_retries=2, dynamic_temperature=True).plan("目标")
    assert seen[0] == 0.7
    assert seen[1] == pytest.approx(0.6)      # 第一次重试降一档
    assert seen[2] == pytest.approx(0.5)      # 再降一档


async def test_planner_keeps_temperature_when_dynamic_is_off():
    seen = []
    good = ('{"steps": [{"id": "s1", "description": "做事", "expected": "一份结果",'
            ' "depends_on": []}]}')

    async def complete(system, user):
        seen.append(_temp())
        return good if len(seen) >= 2 else "不是 JSON"

    await Planner(complete, max_retries=2, dynamic_temperature=False).plan("目标")
    assert seen == [0.7, 0.7]


async def test_planner_restores_temperature_after_planning():
    async def complete(system, user):
        return "坏输出"

    from app.orchestration.planner import PlannerError
    with pytest.raises(PlannerError):
        await Planner(complete, max_retries=1, dynamic_temperature=True).plan("目标")
    assert _temp() == 0.7

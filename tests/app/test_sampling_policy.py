"""温度策略表：角色档、意图档、动态增量的档位与回退。"""
import pytest

from app.sampling_policy import (
    DELTA_PARSE_FAIL_MAX,
    DELTA_RETRY_MAX,
    INTENT_FALLBACK,
    INTENT_TEMPERATURES,
    ROLE_TEMPERATURES,
    intent_temperature,
    parse_fail_delta,
    retry_delta,
    role_temperature,
)


class _Cfg:
    """最小 config 替身：只有策略表用得到的三个字段。"""
    def __init__(self, role=None, intent=None, temperature=0.7):
        self.role_temperatures = role or {}
        self.intent_temperatures = intent or {}
        self.temperature = temperature


# ── 角色档 ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("role", [
    "triage", "critic", "grounding", "judge", "exam_grade", "quiz_grade",
    "plan_finalize", "memory_reconcile", "question_import",
])
def test_judgement_roles_are_deterministic(role):
    """判断/结构化输出一律 0：同一份输入两次判定必须同结论。"""
    assert role_temperature(_Cfg(), role) == 0.0


def test_tool_loop_roles_are_not_zero():
    """带工具的循环不设 0——温度过低更容易卡在重复调同一个工具上。"""
    assert role_temperature(_Cfg(), "executor") >= 0.2
    assert role_temperature(_Cfg(), "planner") >= 0.2


def test_diverse_roles_are_hot():
    """出题与查询改写要发散：低温会让每次出一样的题、几条改写雷同。"""
    assert role_temperature(_Cfg(), "quiz_generate") >= 0.7
    assert role_temperature(_Cfg(), "query_plan") >= 0.4


def test_unknown_role_returns_none():
    assert role_temperature(_Cfg(), "没这个角色") is None


def test_role_override_from_config():
    assert role_temperature(_Cfg(role={"judge": 0.4}), "judge") == 0.4


def test_role_override_is_clamped():
    assert role_temperature(_Cfg(role={"judge": 9}), "judge") == 1.0


def test_role_override_ignores_garbage(caplog):
    assert role_temperature(_Cfg(role={"judge": "热一点"}), "judge") == 0.0   # 保持默认


def test_all_builtin_roles_within_bounds():
    assert all(0.0 <= v <= 1.0 for v in ROLE_TEMPERATURES.values())


# ── 意图档 ─────────────────────────────────────────────────────────────────────
def test_intent_table_values():
    assert intent_temperature(_Cfg(), "factual") == 0.2
    assert intent_temperature(_Cfg(), "creative") == 0.9
    assert intent_temperature(_Cfg(), "chat") == 0.7      # 维持项目原有行为


def test_unknown_intent_falls_back_to_chat():
    """误分类的代价必须有界：认不出就回到 0.7，不赌一个极端值。"""
    assert intent_temperature(_Cfg(), "胡说八道") == INTENT_TEMPERATURES[INTENT_FALLBACK]


def test_intent_override_from_config():
    assert intent_temperature(_Cfg(intent={"creative": 0.75}), "creative") == 0.75


def test_all_builtin_intents_within_bounds():
    assert all(0.0 <= v <= 1.0 for v in INTENT_TEMPERATURES.values())


# ── 动态增量 ───────────────────────────────────────────────────────────────────
def test_retry_delta_first_attempt_is_zero():
    assert retry_delta(0) == 0


def test_retry_delta_steps_up_then_caps():
    assert retry_delta(1) == pytest.approx(0.15)
    assert retry_delta(2) == pytest.approx(0.3)
    assert retry_delta(9) == DELTA_RETRY_MAX          # 档数封顶，不无限升


def test_parse_fail_delta_goes_down_and_floors():
    assert parse_fail_delta(0) == 0
    assert parse_fail_delta(1) == pytest.approx(-0.1)
    assert parse_fail_delta(9) == DELTA_PARSE_FAIL_MAX


def test_retry_and_parse_deltas_point_opposite_ways():
    """打转升温、结构崩了降温——两类失败成因相反，方向也必须相反。"""
    assert retry_delta(1) > 0 > parse_fail_delta(1)

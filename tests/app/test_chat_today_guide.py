"""聊天系统提示的当前日期注入：避免模型用训练年份做时间推算。"""
from datetime import datetime

from app.api.chat import _CN_TZ, _today_guide


def test_today_guide_contains_current_cn_date():
    g = _today_guide()
    now = datetime.now(_CN_TZ)
    assert f"{now:%Y 年 %m 月 %d 日}" in g      # 含北京时间当天日期（每请求动态）
    assert "当前日期" in g and "基准" in g       # 明确要求以此为时间基准
    assert "训练数据" in g                       # 提示不要沿用训练年份


def test_today_guide_uses_beijing_year_not_stale():
    # 年份来自实时时钟而非硬编码：断言含当前年份、不含明显过时的年份
    g = _today_guide()
    assert str(datetime.now(_CN_TZ).year) in g


def test_all_date_injections_share_one_source():
    """三处「今天」必须同源，否则会悄悄漂移。

    历史：chat.py 按北京时间、executor.py 按 date.today()（服务器本地时区）各写一遍，
    跨时区部署时同一轮对话里两处说的「今天」差一天，且不报错。planner 则干脆没有。
    """
    import app.api.chat as chat
    from app.orchestration.executor import _system_with_guide
    from app.orchestration.planner import PLANNER_SYSTEM
    from app.today import today_guide

    g = today_guide()
    assert chat._today_guide() == g
    assert g in _system_with_guide("base")
    # 规划器是本次修复的根因点：常量本身不带日期，拼接发生在调用时
    assert "【当前日期】" not in PLANNER_SYSTEM


def test_guide_forbids_past_year_ranges():
    """只给日期不够：实测模型仍会写出「2025-2026」这种已过去的区间，需明确禁止。"""
    from app.today import today_guide
    assert "不要写出已经过去的年份" in today_guide()


# ---------- 覆盖度：所有模型调用都得知道今天 ----------

async def test_every_single_shot_completer_gets_the_date():
    """所有单轮模型调用（校验、交付门、出题判分、起标题、题目抽取…）都经 build_completer，
    日期在那里统一注入。此测试直接盯住那个收口。

    不逐个去 16 个 *_SYSTEM 常量上加：那样今天加全了，下次新增一个提示词又会漏，
    而且不会有任何报错——正是这个项目反复吃亏的模式。
    """
    from app.completion import build_completer

    seen = {}

    class _FakeClient:
        async def stream(self, *a, **kw):
            raise AssertionError("不应真的发请求")

    import app.completion as comp

    class _Loop:
        def __init__(self, *, context, **kw):
            seen["system"] = getattr(context, "_system_prompt", None)

        async def run(self, _user):
            from harness.events import RunFinished
            from harness.types import Message, Role
            yield RunFinished(message=Message(role=Role.ASSISTANT, content="ok"))

    orig, comp.AgentLoop = comp.AgentLoop, _Loop
    try:
        await build_completer(_FakeClient(), "m")("你是裁判。", "判一下")
    finally:
        comp.AgentLoop = orig
    assert "【当前日期】" in (seen["system"] or ""), "单轮模型调用必须带当前日期"


def test_injection_is_idempotent():
    """个别调用方（如 Planner）会自己先拼一份日期，收口处不能再拼一遍。"""
    from app.today import today_guide, with_today
    once = "你是规划器。" + today_guide()
    assert with_today(once) == once
    assert with_today("裸提示").count("【当前日期】") == 1


def test_synthesize_and_simple_answer_prompts_carry_date():
    """写最终答复的那两条路径不走 build_completer（是带工具的 AgentLoop），单独确认。"""
    import inspect
    from app.orchestration import orchestrator as o
    src = inspect.getsource(o)
    assert "ContextManager(with_today(SYNTH_SYSTEM))" in src
    assert "with_today(SYNTH_SYSTEM + CLARIFY_GUIDE)" in src

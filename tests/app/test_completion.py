import pytest
from app.completion import (
    build_completer, _alt_config, build_judge_completer, build_fast_completer,
    build_check_completer)
from app.config import AppConfig


@pytest.mark.asyncio
async def test_completer_returns_final_text(make_mock, text_turn):
    complete = build_completer(make_mock([text_turn("你好世界")]), model_name="m")
    out = await complete("你是助手", "打个招呼")
    assert out == "你好世界"


@pytest.mark.asyncio
async def test_completer_raises_on_run_error():
    class Boom:
        async def stream(self, messages, tools):
            raise RuntimeError("boom")
            yield  # 成为异步生成器
    complete = build_completer(Boom(), model_name="m")
    with pytest.raises(RuntimeError):
        await complete("s", "u")


# ---- 独立角色模型装配（judge / summary 共用 _alt_config 的回退语义）----

def _jcfg(**kw):
    return AppConfig(api_key="mainkey", base_url="https://main/v1", model="main-model",
                     app_db_path=":memory:", _env_file=None, **kw)


def _alt(cfg, role: str):
    """按 judge_/fast_ 前缀取该角色的三件套，喂给 _alt_config。"""
    return _alt_config(cfg, getattr(cfg, f"{role}_model"),
                       getattr(cfg, f"{role}_base_url"), getattr(cfg, f"{role}_api_key"))


@pytest.mark.parametrize("role", ["judge", "fast"])
def test_alt_config_none_when_unset(role):
    assert _alt(_jcfg(), role) is None                            # 未配 → 回退主模型


@pytest.mark.parametrize("role", ["judge", "fast"])
def test_alt_config_model_only_falls_back_endpoint(role):
    cfg = _alt(_jcfg(**{f"{role}_model": "alt-model"}), role)
    assert cfg.model == "alt-model"
    assert cfg.base_url == "https://main/v1" and cfg.api_key == "mainkey"


@pytest.mark.parametrize("role", ["judge", "fast"])
def test_alt_config_independent_endpoint(role):
    cfg = _alt(_jcfg(**{f"{role}_model": "am", f"{role}_base_url": "https://alt/v1",
                        f"{role}_api_key": "akey"}), role)
    assert (cfg.model, cfg.base_url, cfg.api_key) == ("am", "https://alt/v1", "akey")


@pytest.mark.parametrize("role", ["judge", "fast"])
def test_alt_config_does_not_mutate_main(role):
    cfg = _jcfg(**{f"{role}_model": "am", f"{role}_base_url": "https://alt/v1"})
    _alt(cfg, role)
    assert cfg.model == "main-model" and cfg.base_url == "https://main/v1"


def test_build_judge_completer_returns_callable_both_branches():
    assert callable(build_judge_completer(object(), _jcfg()))                  # 回退主 client
    assert callable(build_judge_completer(object(), _jcfg(judge_model="jm")))  # 起独立 client


@pytest.mark.asyncio
async def test_judge_completer_forces_thinking_off(make_mock, text_turn):
    # 外层即使开了思考，judge 调用也应强制 enable_thinking=False
    from harness.llm.openai_compat import (
        get_extra_body_override, set_extra_body_override, reset_extra_body_override)
    seen = {}
    inner = make_mock([text_turn("ok")])
    orig = inner.stream

    async def wrapped(messages, tools):
        seen["thinking"] = get_extra_body_override().get("enable_thinking")
        async for c in orig(messages, tools):
            yield c
    inner.stream = wrapped

    tok = set_extra_body_override({"enable_thinking": True})
    try:
        await build_judge_completer(inner, _jcfg())("s", "u")   # judge_model 空 → 回退 inner
    finally:
        reset_extra_body_override(tok)
    assert seen["thinking"] is False


# ---- 快速模型档 completer ----

def _spy_thinking(inner):
    """截下调用时 extra_body 覆盖里的 enable_thinking。"""
    seen = {}
    orig = inner.stream

    async def wrapped(messages, tools):
        from harness.llm.openai_compat import get_extra_body_override
        seen["thinking"] = get_extra_body_override().get("enable_thinking")
        async for c in orig(messages, tools):
            yield c
    inner.stream = wrapped
    return seen


def test_build_fast_completer_returns_callable_both_branches():
    assert callable(build_fast_completer(object(), _jcfg()))                    # 回退主 client
    assert callable(build_fast_completer(object(), _jcfg(fast_model="sm")))  # 独立 client


@pytest.mark.asyncio
async def test_fast_completer_disables_thinking_even_when_falling_back_to_main(
        make_mock, text_turn):
    """没配 FAST_MODEL、回退主模型时，也必须显式关思考。

    关思考与换模型是两件独立的事：不配独立模型的人同样该省下这份思考开销。
    此前根本不发 enable_thinking 这个键，由模型服务端默认决定（Qwen3 系默认开）。
    """
    inner = make_mock([text_turn("摘要")])
    seen = _spy_thinking(inner)
    await build_fast_completer(inner, _jcfg())("s", "u")   # _jcfg() 未配 fast_model → 回退 inner
    assert seen["thinking"] is False


def test_fast_tier_has_no_thinking_switch():
    """快速档不该有思考开关：机械活开思考是自相矛盾的组合，配置项只留给真取舍。
    留着它就是留一把「把快速档配成慢档」的枪（同 judge 档，也不给开关）。"""
    assert not hasattr(_jcfg(), "fast_enable_thinking")


@pytest.mark.asyncio
async def test_fast_completer_ignores_ambient_toggle(make_mock, text_turn):
    """外层（聊天页开关）即使开着思考，快速档也恒关——它本就够不着那个开关。"""
    from harness.llm.openai_compat import set_extra_body_override, reset_extra_body_override
    inner = make_mock([text_turn("摘要")])
    seen = _spy_thinking(inner)
    tok = set_extra_body_override({"enable_thinking": True})
    try:
        await build_fast_completer(inner, _jcfg())("s", "u")
    finally:
        reset_extra_body_override(tok)
    assert seen["thinking"] is False


@pytest.mark.asyncio
async def test_fast_completer_restores_override_after_call(make_mock, text_turn):
    """调用后须还原：摘要跑在上下文组装阶段，泄漏出去会污染后续本轮任务的模型调用。"""
    from harness.llm.openai_compat import (
        get_extra_body_override, set_extra_body_override, reset_extra_body_override)
    tok = set_extra_body_override({"enable_thinking": True, "top_p": 0.9})
    try:
        await build_fast_completer(make_mock([text_turn("摘要")]), _jcfg())("s", "u")
        assert get_extra_body_override() == {"enable_thinking": True, "top_p": 0.9}
    finally:
        reset_extra_body_override(tok)


# ---- 核对档（grounding）：主模型 + 关思考 ----

@pytest.mark.asyncio
async def test_check_completer_disables_thinking(make_mock, text_turn):
    """grounding 与 judge 同为交付门的校验动作，行为须一致——都不带思考链。

    此前 grounding 用的是不带覆盖的 build_completer，实际继承了聊天页那个思考开关，
    于是同一个 AnswerVerifier 里打分恒关、grounding 却跟着用户开关走。
    """
    inner = make_mock([text_turn("ok")])
    seen = _spy_thinking(inner)
    await build_check_completer(inner, _jcfg())("s", "u")
    assert seen["thinking"] is False


@pytest.mark.asyncio
async def test_check_completer_ignores_ambient_toggle(make_mock, text_turn):
    from harness.llm.openai_compat import set_extra_body_override, reset_extra_body_override
    inner = make_mock([text_turn("ok")])
    seen = _spy_thinking(inner)
    tok = set_extra_body_override({"enable_thinking": True})
    try:
        await build_check_completer(inner, _jcfg())("s", "u")
    finally:
        reset_extra_body_override(tok)
    assert seen["thinking"] is False


def test_check_completer_stays_on_main_model_not_judge():
    """核对不该占用 judge 档：它是对着原文核事实，没有「给自己打高分」的偏差可言，
    而 judge 档往往指向更贵的模型。"""
    cfg = _jcfg(judge_model="expensive-judge")
    # 配了 judge_model 也不影响核对档：它只认主模型，故两分支构造出的都是可调用对象
    assert callable(build_check_completer(object(), cfg))
    assert callable(build_check_completer(object(), _jcfg()))

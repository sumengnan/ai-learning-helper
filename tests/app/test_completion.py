import pytest
from app.completion import (
    build_completer, _alt_config, build_judge_completer, build_summary_completer)
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
    """按 judge_/summary_ 前缀取该角色的三件套，喂给 _alt_config。"""
    return _alt_config(cfg, getattr(cfg, f"{role}_model"),
                       getattr(cfg, f"{role}_base_url"), getattr(cfg, f"{role}_api_key"))


@pytest.mark.parametrize("role", ["judge", "summary"])
def test_alt_config_none_when_unset(role):
    assert _alt(_jcfg(), role) is None                            # 未配 → 回退主模型


@pytest.mark.parametrize("role", ["judge", "summary"])
def test_alt_config_model_only_falls_back_endpoint(role):
    cfg = _alt(_jcfg(**{f"{role}_model": "alt-model"}), role)
    assert cfg.model == "alt-model"
    assert cfg.base_url == "https://main/v1" and cfg.api_key == "mainkey"


@pytest.mark.parametrize("role", ["judge", "summary"])
def test_alt_config_independent_endpoint(role):
    cfg = _alt(_jcfg(**{f"{role}_model": "am", f"{role}_base_url": "https://alt/v1",
                        f"{role}_api_key": "akey"}), role)
    assert (cfg.model, cfg.base_url, cfg.api_key) == ("am", "https://alt/v1", "akey")


@pytest.mark.parametrize("role", ["judge", "summary"])
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


# ---- 摘要 completer ----

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


def test_build_summary_completer_returns_callable_both_branches():
    assert callable(build_summary_completer(object(), _jcfg()))                    # 回退主 client
    assert callable(build_summary_completer(object(), _jcfg(summary_model="sm")))  # 独立 client


@pytest.mark.asyncio
async def test_summary_completer_disables_thinking_by_default(make_mock, text_turn):
    """默认关思考：压缩历史不需要推理链。此前不发这个键，由模型服务端默认决定（Qwen3 系默认开）。"""
    inner = make_mock([text_turn("摘要")])
    seen = _spy_thinking(inner)
    await build_summary_completer(inner, _jcfg())("s", "u")
    assert seen["thinking"] is False


@pytest.mark.asyncio
async def test_summary_completer_honours_enable_thinking_config(make_mock, text_turn):
    inner = make_mock([text_turn("摘要")])
    seen = _spy_thinking(inner)
    await build_summary_completer(inner, _jcfg(summary_enable_thinking=True))("s", "u")
    assert seen["thinking"] is True


@pytest.mark.asyncio
async def test_summary_thinking_config_wins_over_ambient_override(make_mock, text_turn):
    """外层（聊天页开关）即使开着思考，摘要也按自己的配置走——它本就够不着那个开关。"""
    from harness.llm.openai_compat import set_extra_body_override, reset_extra_body_override
    inner = make_mock([text_turn("摘要")])
    seen = _spy_thinking(inner)
    tok = set_extra_body_override({"enable_thinking": True})
    try:
        await build_summary_completer(inner, _jcfg())("s", "u")
    finally:
        reset_extra_body_override(tok)
    assert seen["thinking"] is False


@pytest.mark.asyncio
async def test_summary_completer_restores_override_after_call(make_mock, text_turn):
    """调用后须还原：摘要跑在上下文组装阶段，泄漏出去会污染后续本轮任务的模型调用。"""
    from harness.llm.openai_compat import (
        get_extra_body_override, set_extra_body_override, reset_extra_body_override)
    tok = set_extra_body_override({"enable_thinking": True, "top_p": 0.9})
    try:
        await build_summary_completer(make_mock([text_turn("摘要")]), _jcfg())("s", "u")
        assert get_extra_body_override() == {"enable_thinking": True, "top_p": 0.9}
    finally:
        reset_extra_body_override(tok)

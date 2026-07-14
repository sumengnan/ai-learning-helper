import pytest
from app.completion import build_completer, _judge_config, build_judge_completer
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


# ---- 独立 judge 模型装配 ----

def _jcfg(**kw):
    return AppConfig(api_key="mainkey", base_url="https://main/v1", model="main-model",
                     app_db_path=":memory:", _env_file=None, **kw)


def test_judge_config_none_when_unset():
    assert _judge_config(_jcfg()) is None                         # 未配 → 回退主模型


def test_judge_config_model_only_falls_back_endpoint():
    jcfg = _judge_config(_jcfg(judge_model="judge-model"))
    assert jcfg.model == "judge-model"
    assert jcfg.base_url == "https://main/v1" and jcfg.api_key == "mainkey"


def test_judge_config_independent_endpoint():
    jcfg = _judge_config(_jcfg(judge_model="jm", judge_base_url="https://judge/v1",
                               judge_api_key="jkey"))
    assert (jcfg.model, jcfg.base_url, jcfg.api_key) == ("jm", "https://judge/v1", "jkey")


def test_judge_config_does_not_mutate_main():
    cfg = _jcfg(judge_model="jm", judge_base_url="https://judge/v1")
    _judge_config(cfg)
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

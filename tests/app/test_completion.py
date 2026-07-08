import pytest
from app.completion import build_completer


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

# tests/app/test_titling.py
from app.titling import make_title


def _completer(reply=None, exc=None):
    async def c(system, user):
        if exc is not None:
            raise exc
        return reply
    return c


async def test_uses_model_title():
    t = await make_title(_completer("光合作用原理"), "那个……光合作用到底咋回事啊")
    assert t == "光合作用原理"


async def test_strips_quotes_and_punctuation():
    t = await make_title(_completer("《二叉树遍历》。"), "问下二叉树怎么遍历")
    assert t == "二叉树遍历"


async def test_truncates_to_max_len():
    t = await make_title(_completer("一二三四五六七八九十十一十二十三"), "很长的问题", max_len=12)
    assert len(t) == 12


async def test_empty_model_output_falls_back_to_message():
    t = await make_title(_completer("   "), "牛顿第一定律是什么")
    assert t == "牛顿第一定律是什么"[:12]


async def test_model_error_falls_back():
    t = await make_title(_completer(exc=RuntimeError("boom")), "勾股定理证明")
    assert t == "勾股定理证明"


async def test_all_empty_returns_default():
    t = await make_title(_completer(""), "   ")
    assert t == "新对话"

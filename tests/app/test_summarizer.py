import pytest

from app.summaries import SummaryStore
from app.summarizer import RollingSummarizer
from harness.types import Message, Role


def _store():
    return SummaryStore(":memory:")


def _turn(u: str, a: str) -> list[Message]:
    return [Message(role=Role.USER, content=u), Message(role=Role.ASSISTANT, content=a)]


class _FakeCompleter:
    """记录调用次数的假补全器；把新增 delta 拼进摘要，便于断言水位与内容。"""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        return f"摘要#{self.calls}"


async def test_first_summary_advances_watermark():
    store, fake = _store(), _FakeCompleter()
    s = RollingSummarizer(store, fake, model="gpt-4o-mini", max_summary_tokens=2000)
    evicted = _turn("轮1问", "轮1答") + _turn("轮2问", "轮2答")  # 4 条
    out = await s.ensure("c1", evicted)
    assert out == "摘要#1"
    assert fake.calls == 1
    rec = store.get("c1")
    assert rec.up_to_seq == 4          # 水位=已覆盖前缀长度
    assert rec.summary == "摘要#1"


async def test_no_delta_skips_llm_call():
    store, fake = _store(), _FakeCompleter()
    s = RollingSummarizer(store, fake, model="gpt-4o-mini", max_summary_tokens=2000)
    evicted = _turn("轮1问", "轮1答")
    await s.ensure("c1", evicted)            # 首次，水位=2
    calls_after_first = fake.calls
    out = await s.ensure("c1", evicted)      # 同样的 evicted，无新增
    assert fake.calls == calls_after_first   # 未再调用 LLM
    assert out == "摘要#1"


async def test_incremental_only_summarizes_delta():
    store, fake = _store(), _FakeCompleter()
    s = RollingSummarizer(store, fake, model="gpt-4o-mini", max_summary_tokens=2000)
    captured = {}

    async def capture(system_prompt: str, user_prompt: str) -> str:
        fake.calls += 1
        captured["prompt"] = user_prompt
        return f"摘要#{fake.calls}"

    s2 = RollingSummarizer(store, capture, model="gpt-4o-mini", max_summary_tokens=2000)
    await s2.ensure("c1", _turn("轮1问", "轮1答"))                       # 水位=2
    await s2.ensure("c1", _turn("轮1问", "轮1答") + _turn("轮2问", "轮2答"))  # 新增轮2
    # 第二次的 prompt 应只含 delta（轮2），不含轮1原文
    assert "轮2问" in captured["prompt"]
    assert "轮1问" not in captured["prompt"]
    assert store.get("c1").up_to_seq == 4


async def test_empty_evicted_returns_none_no_call():
    store, fake = _store(), _FakeCompleter()
    s = RollingSummarizer(store, fake, model="gpt-4o-mini", max_summary_tokens=2000)
    out = await s.ensure("c1", [])
    assert out is None
    assert fake.calls == 0

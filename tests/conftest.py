from __future__ import annotations

import pytest

from harness.llm.base import StreamChunk, ToolCallDelta
from harness.usage import Usage


class MockModelClient:
    """脚本化的 ModelClient 测试替身。

    turns：一个列表，每个元素是"一轮"要 yield 的 StreamChunk 列表。
    每次调用 stream() 消费下一轮。
    """

    def __init__(self, turns: list[list[StreamChunk]]):
        self._turns = list(turns)
        self._i = 0

    async def stream(self, messages, tools):
        turn = self._turns[self._i]
        self._i += 1
        for chunk in turn:
            yield chunk


def _text_turn(text: str) -> list[StreamChunk]:
    # 拆成两个 chunk，模拟流式增量
    mid = max(1, len(text) // 2)
    return [
        StreamChunk(type="text", text=text[:mid]),
        StreamChunk(type="text", text=text[mid:]),
        StreamChunk(type="done"),
    ]


def _tool_turn(name: str, arguments_json: str, call_id: str = "c1") -> list[StreamChunk]:
    # 参数分两段发，验证累加逻辑
    mid = max(1, len(arguments_json) // 2)
    return [
        StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
            index=0, id=call_id, name=name, arguments=arguments_json[:mid])),
        StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
            index=0, arguments=arguments_json[mid:])),
        StreamChunk(type="done"),
    ]


@pytest.fixture
def make_mock():
    return lambda turns: MockModelClient(turns)


@pytest.fixture
def text_turn():
    return _text_turn


@pytest.fixture
def tool_turn():
    return _tool_turn


def _done_with_usage(prompt=10, completion=5, attempts=1):
    return StreamChunk(type="done", usage=Usage(prompt, completion, prompt + completion), attempts=attempts)


def _text_turn_usage(text: str, prompt=10, completion=5):
    return [StreamChunk(type="text", text=text), _done_with_usage(prompt, completion)]


@pytest.fixture
def done_with_usage():
    return _done_with_usage


@pytest.fixture
def text_turn_usage():
    return _text_turn_usage


class FlakyModelClient:
    """前 fail_times 次调用 stream 抛 transient_exc，之后正常吐 turn。

    mid_stream=True 时改为：先 yield 一个 chunk 再抛（模拟流中途断裂，不可重试）。
    """

    def __init__(self, transient_exc, turn, fail_times=1, mid_stream=False):
        self._exc = transient_exc
        self._turn = turn
        self._fail_times = fail_times
        self._mid_stream = mid_stream
        self.calls = 0

    async def stream(self, messages, tools):
        self.calls += 1
        if self.calls <= self._fail_times:
            if self._mid_stream:
                yield StreamChunk(type="text", text="半截")
            raise self._exc
        for chunk in self._turn:
            yield chunk


@pytest.fixture
def flaky_client():
    return FlakyModelClient

from __future__ import annotations

import pytest

from harness.llm.base import StreamChunk, ToolCallDelta


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

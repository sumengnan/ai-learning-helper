import pytest

from harness.llm.openai_compat import OpenAICompatibleClient
from harness.config import HarnessConfig
from harness.types import Message, Role


class _FakeDelta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, delta):
        self.delta = delta


class _FakeEvent:
    def __init__(self, delta):
        self.choices = [_FakeChoice(delta)]


class _FakeToolCall:
    def __init__(self, index, id, name, arguments):
        self.index = index
        self.id = id
        self.function = type("F", (), {"name": name, "arguments": arguments})()


async def _fake_stream(events):
    for e in events:
        yield e


async def test_stream_normalizes_text_and_tool_and_done(monkeypatch):
    cfg = HarnessConfig(api_key="k")
    client = OpenAICompatibleClient(cfg)

    events = [
        _FakeEvent(_FakeDelta(content="你好")),
        _FakeEvent(_FakeDelta(tool_calls=[_FakeToolCall(0, "c1", "calculator", '{"e')])),
        _FakeEvent(_FakeDelta(tool_calls=[_FakeToolCall(0, None, None, 'xp": "1+1"}')])),
    ]

    async def fake_create(**kwargs):
        assert kwargs["stream"] is True
        assert kwargs["messages"][0]["role"] == "user"
        return _fake_stream(events)

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)

    out = [c async for c in client.stream([Message(role=Role.USER, content="hi")], [])]
    assert out[0].type == "text" and out[0].text == "你好"
    assert out[1].type == "tool_call" and out[1].tool_call_delta.name == "calculator"
    assert out[2].tool_call_delta.arguments == 'xp": "1+1"}'
    assert out[-1].type == "done"

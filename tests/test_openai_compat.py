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


class _FakeUsage:
    def __init__(self, p, c, t):
        self.prompt_tokens = p
        self.completion_tokens = c
        self.total_tokens = t


class _FakeEventUsage:
    """带 usage、无 choices 的尾 chunk（include_usage 行为）。"""
    def __init__(self, usage):
        self.choices = []
        self.usage = usage


async def test_done_chunk_uses_real_usage(monkeypatch):
    cfg = HarnessConfig(api_key="k")
    client = OpenAICompatibleClient(cfg)
    events = [
        _FakeEvent(_FakeDelta(content="你好")),
        _FakeEventUsage(_FakeUsage(11, 7, 18)),
    ]

    async def fake_create(**kwargs):
        assert kwargs["stream_options"] == {"include_usage": True}
        return _fake_stream(events)

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)
    out = [c async for c in client.stream([Message(role=Role.USER, content="hi")], [])]
    done = [c for c in out if c.type == "done"][0]
    assert (done.usage.prompt_tokens, done.usage.completion_tokens, done.usage.total_tokens) == (11, 7, 18)


async def test_done_chunk_falls_back_to_tiktoken(monkeypatch):
    cfg = HarnessConfig(api_key="k")
    client = OpenAICompatibleClient(cfg)
    events = [_FakeEvent(_FakeDelta(content="hello world"))]  # 无 usage 尾 chunk

    async def fake_create(**kwargs):
        return _fake_stream(events)

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)
    out = [c async for c in client.stream([Message(role=Role.USER, content="hi")], [])]
    done = [c for c in out if c.type == "done"][0]
    assert done.usage is not None
    assert done.usage.total_tokens > 0   # tiktoken 估算


async def test_extra_body_passed_when_configured(monkeypatch):
    cfg = HarnessConfig(api_key="k", llm_extra_body={"enable_thinking": False})
    client = OpenAICompatibleClient(cfg)
    events = [_FakeEvent(_FakeDelta(content="hi"))]

    async def fake_create(**kwargs):
        assert kwargs["extra_body"] == {"enable_thinking": False}   # 透传
        return _fake_stream(events)

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)
    [c async for c in client.stream([Message(role=Role.USER, content="hi")], [])]


async def test_extra_body_omitted_by_default(monkeypatch):
    cfg = HarnessConfig(api_key="k")            # 默认空 → 不加 extra_body（零行为变更）
    client = OpenAICompatibleClient(cfg)
    events = [_FakeEvent(_FakeDelta(content="hi"))]

    async def fake_create(**kwargs):
        assert "extra_body" not in kwargs
        return _fake_stream(events)

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)
    [c async for c in client.stream([Message(role=Role.USER, content="hi")], [])]


async def test_include_usage_disabled_omits_stream_options(monkeypatch):
    cfg = HarnessConfig(api_key="k", include_usage=False)
    client = OpenAICompatibleClient(cfg)
    events = [_FakeEvent(_FakeDelta(content="hello world"))]  # 无 usage 尾 chunk

    async def fake_create(**kwargs):
        assert "stream_options" not in kwargs
        return _fake_stream(events)

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)
    out = [c async for c in client.stream([Message(role=Role.USER, content="hi")], [])]
    done = [c for c in out if c.type == "done"][0]
    assert done.usage is not None
    assert done.usage.total_tokens > 0   # tiktoken 兜底

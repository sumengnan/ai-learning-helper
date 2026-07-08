from harness.usage import Usage, estimate_usage, cost_usd
from harness.types import Message, Role


def test_usage_add():
    a = Usage(1, 2, 3)
    b = Usage(10, 20, 30)
    c = a + b
    assert (c.prompt_tokens, c.completion_tokens, c.total_tokens) == (11, 22, 33)


def test_estimate_usage_nonzero():
    u = estimate_usage([Message(role=Role.USER, content="hello world")], "hi there", "gpt-4o-mini")
    assert u.prompt_tokens > 0
    assert u.completion_tokens > 0
    assert u.total_tokens == u.prompt_tokens + u.completion_tokens


def test_cost_usd_with_price():
    u = Usage(1000, 1000, 2000)
    assert cost_usd(u, "m", {"m": [1.0, 2.0]}) == 3.0


def test_cost_usd_without_price_is_none():
    assert cost_usd(Usage(1, 1, 2), "m", {}) is None

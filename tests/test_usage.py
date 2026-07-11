from harness.usage import Usage, estimate_usage, cost_usd, count_message_tokens
from harness.types import Message, Role, ToolCall


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


_M = "gpt-4o-mini"


def test_count_message_tokens_positive_and_monotonic():
    one = count_message_tokens([Message(role=Role.USER, content="讲讲二叉树")], _M)
    two = count_message_tokens(
        [Message(role=Role.USER, content="讲讲二叉树"),
         Message(role=Role.ASSISTANT, content="二叉树是每个节点最多两个子节点的树结构。")], _M)
    assert one > 0
    assert two > one  # 更多消息 → 更多 token


def test_count_message_tokens_includes_tool_calls():
    """关键回归：tool_calls 必须计入，estimate_usage 旧实现漏了它。"""
    plain = Message(role=Role.ASSISTANT, content="好的")
    with_tc = Message(
        role=Role.ASSISTANT, content="好的",
        tool_calls=[ToolCall(id="c1", name="run_python",
                             arguments={"code": "print(sum(range(100)))"})])
    assert count_message_tokens([with_tc], _M) > count_message_tokens([plain], _M)


def test_count_message_tokens_tool_result_message():
    msg = Message(role=Role.TOOL, content="4950", tool_call_id="c1")
    assert count_message_tokens([msg], _M) > 0


def test_count_message_tokens_image_part_not_encoded_as_text():
    """多模态图片按固定近似计，不能把 base64 当文本编码（否则爆表）。"""
    huge_data_url = "data:image/png;base64," + "A" * 100_000
    img_msg = Message(role=Role.USER, content=[
        {"type": "text", "text": "看这张图"},
        {"type": "image_url", "image_url": {"url": huge_data_url}},
    ])
    n = count_message_tokens([img_msg], _M)
    # 图片按固定近似（数百 token），远小于把 10 万字符 base64 当文本编码的量
    assert 100 < n < 5000


def test_count_message_tokens_empty():
    assert count_message_tokens([], _M) >= 0

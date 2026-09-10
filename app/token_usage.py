"""Token counting helpers for user/model text.

`tiktoken` treats strings such as ``<|endoftext|>`` as reserved special
tokens and rejects them by default.  They are valid ordinary text in a chat
message, though, so context budgeting must count them without changing the
message that is sent to the model.
"""
from __future__ import annotations

import copy
import re

from harness.types import Message
from harness.usage import count_message_tokens

_SPECIAL_TOKEN_RE = re.compile(r"<\|[^|]*\|>")


def _escape_special_tokens(value):
    if isinstance(value, str):
        # Adding a space makes the marker ordinary text for tiktoken while
        # keeping the fallback count close to the original text length.
        return _SPECIAL_TOKEN_RE.sub(lambda m: f"< |{m.group(0)[2:]}", value)
    if isinstance(value, dict):
        return {k: _escape_special_tokens(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_escape_special_tokens(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_escape_special_tokens(v) for v in value)
    return value


def message_for_token_count(message: Message) -> Message:
    """Return a copy safe for tiktoken counting, without mutating ``message``."""
    result = copy.deepcopy(message)
    result.content = _escape_special_tokens(result.content)
    for tool_call in result.tool_calls:
        tool_call.name = _escape_special_tokens(tool_call.name)
        tool_call.arguments = _escape_special_tokens(tool_call.arguments)
    result.tool_call_id = _escape_special_tokens(result.tool_call_id)
    return result


def count_message_tokens_safe(messages: list[Message], model: str) -> int:
    """Count messages while treating reserved token-looking text as ordinary text."""
    try:
        return count_message_tokens(messages, model)
    except ValueError as exc:
        if "disallowed special token" not in str(exc):
            raise
        safe_messages = [message_for_token_count(message) for message in messages]
        return count_message_tokens(safe_messages, model)

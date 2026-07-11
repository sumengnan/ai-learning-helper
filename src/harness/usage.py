# src/harness/usage.py
from __future__ import annotations

import json
from dataclasses import dataclass

from .types import Message


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.total_tokens + other.total_tokens,
        )


def _get_encoding(model: str):
    import tiktoken

    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def _encode_len(text: str, model: str) -> int:
    return len(_get_encoding(model).encode(text))


# 按 OpenAI chat 计费口径的固定开销：每条消息的结构包裹 + 回复引导。
_TOKENS_PER_MESSAGE = 3
_TOKENS_REPLY_PRIMING = 3
# 低精度图片的近似 token 下限；避免把 base64 data URL 当文本编码撑爆预算。
_IMAGE_TOKENS = 765


def _count_content_tokens(content, enc) -> int:
    """content 可为 str / None / 多模态 content-parts 列表。"""
    if content is None:
        return 0
    if isinstance(content, str):
        return len(enc.encode(content))
    total = 0
    for part in content:
        if not isinstance(part, dict):
            total += len(enc.encode(str(part)))
            continue
        ptype = part.get("type")
        if ptype == "text":
            total += len(enc.encode(part.get("text", "")))
        elif ptype == "image_url":
            total += _IMAGE_TOKENS  # 固定近似，不编码 base64 本身
        else:
            total += len(enc.encode(json.dumps(part, ensure_ascii=False)))
    return total


def count_message_tokens(messages: list[Message], model: str) -> int:
    """精确估算一组消息作为 prompt 的 token 数（含 tool_calls 与多模态图片）。

    比 estimate_usage 更完整：estimate_usage 只算了 m.content，漏了 tool_calls，
    也会把图片 base64 当文本。上下文预算裁剪需要这个更贴近真实计费的口径。
    """
    if not messages:
        return 0
    enc = _get_encoding(model)
    total = 0
    for m in messages:
        total += _TOKENS_PER_MESSAGE
        total += len(enc.encode(m.role.value))
        total += _count_content_tokens(m.content, enc)
        for tc in m.tool_calls:
            total += len(enc.encode(tc.name))
            total += len(enc.encode(json.dumps(tc.arguments, ensure_ascii=False)))
            total += 4  # 每个 tool_call 的结构包裹近似
        if m.tool_call_id:
            total += len(enc.encode(m.tool_call_id))
    total += _TOKENS_REPLY_PRIMING
    return total


def estimate_usage(messages: list[Message], completion: str, model: str) -> Usage:
    """端点不返回真实 usage 时的 tiktoken 估算（近似值，仅用于预算保护）。"""
    prompt_text = "\n".join(m.content or "" for m in messages)
    p = _encode_len(prompt_text, model)
    c = _encode_len(completion, model)
    return Usage(prompt_tokens=p, completion_tokens=c, total_tokens=p + c)


def cost_usd(usage: Usage, model: str, price_map: dict) -> float | None:
    price = price_map.get(model)
    if not price:
        return None
    in_per_1k, out_per_1k = price
    return usage.prompt_tokens / 1000 * in_per_1k + usage.completion_tokens / 1000 * out_per_1k

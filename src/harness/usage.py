# src/harness/usage.py
from __future__ import annotations

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


def _encode_len(text: str, model: str) -> int:
    import tiktoken

    try:
        enc = tiktoken.encoding_for_model(model)
    except KeyError:
        enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(text))


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

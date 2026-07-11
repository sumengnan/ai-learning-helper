# src/harness/memory/reranker.py
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Reranker(Protocol):
    """精排可插拔切点。默认 NoOpReranker；日后接 LLM/cross-encoder 实现同签名。"""

    async def rerank(self, query: str, candidates: list) -> list:
        ...


class NoOpReranker:
    """不改序，原样返回候选。"""

    async def rerank(self, query: str, candidates: list) -> list:
        return candidates

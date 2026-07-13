# src/harness/memory/reranker.py
from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

import httpx

logger = logging.getLogger("harness.memory.reranker")


@runtime_checkable
class Reranker(Protocol):
    """精排可插拔切点。默认 NoOpReranker；日后接 LLM/cross-encoder 实现同签名。"""

    async def rerank(self, query: str, candidates: list) -> list:
        ...


class NoOpReranker:
    """不改序，原样返回候选。"""

    async def rerank(self, query: str, candidates: list) -> list:
        return candidates


class HttpReranker:
    """调用 OpenAI/Cohere/Jina 兼容的 rerank REST 端点做精排。

    请求：POST {base_url}/rerank  {model, query, documents, [top_n]}
    响应：{"results": [{"index": int, "relevance_score": float}, ...]}

    失败哲学：网络/超时/HTTP/格式异常一律降级为「原序返回」，绝不打断检索或聊天
    （与 L3 检索「失败不影响本轮」一致）。top_n 截断时命中的排前、未命中的按原序接尾，
    不丢候选，最终 top-k 截断仍交给 Retriever。
    """

    def __init__(self, base_url: str, api_key: str, model: str, *,
                 timeout: float = 30.0, top_n: int | None = None) -> None:
        self._url = base_url.rstrip("/") + "/rerank"
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._top_n = top_n

    async def rerank(self, query: str, candidates: list) -> list:
        if len(candidates) <= 1 or not query or not query.strip():
            return candidates
        documents = [_doc_text(c) for c in candidates]
        payload: dict = {"model": self._model, "query": query, "documents": documents}
        if self._top_n:
            payload["top_n"] = min(self._top_n, len(documents))
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(self._url, json=payload, headers=headers)
                resp.raise_for_status()
                results = resp.json().get("results", [])
        except Exception as e:  # 网络/超时/HTTP/格式错误一律降级保序
            logger.warning("rerank 端点调用失败，退回原序：%s", e)
            return candidates
        return _reorder(candidates, results)


def _doc_text(candidate) -> str:
    """取候选正文用于打分。兼容 ScoredHit（.record.text）与裸字符串。"""
    rec = getattr(candidate, "record", None)
    if rec is not None:
        return getattr(rec, "text", "") or ""
    return candidate if isinstance(candidate, str) else str(candidate)


def _reorder(candidates: list, results: list) -> list:
    """按 results 的 relevance_score 降序重排：命中的排前，未命中的按原序接尾（不丢候选）。"""
    scored: list[tuple[int, float]] = []
    for r in results:
        idx = r.get("index")
        if isinstance(idx, int) and 0 <= idx < len(candidates):
            scored.append((idx, r.get("relevance_score", 0.0)))
    if not scored:                        # 端点未给可用结果 → 原序
        return candidates
    scored.sort(key=lambda t: t[1], reverse=True)
    seen = {idx for idx, _ in scored}
    ordered = [candidates[idx] for idx, _ in scored]
    ordered += [c for i, c in enumerate(candidates) if i not in seen]
    return ordered

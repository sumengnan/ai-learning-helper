from __future__ import annotations

from typing import Protocol, runtime_checkable

from openai import AsyncOpenAI


@runtime_checkable
class EmbeddingClient(Protocol):
    dimension: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class OpenAICompatibleEmbeddingClient:
    """调用 OpenAI 兼容 /embeddings 端点。端点与聊天端点独立配置。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        dimension: int,
        timeout: float = 60.0,
    ) -> None:
        self.dimension = dimension
        self._model = model
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        resp = await self._client.embeddings.create(model=self._model, input=texts)
        return [d.embedding for d in resp.data]

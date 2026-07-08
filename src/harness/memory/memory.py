from __future__ import annotations

from .chunker import chunk
from .embeddings import EmbeddingClient
from .store import MemoryHit, MemoryStore


class Memory:
    """串起 chunker + embedder + store 的门面。唯一同时知道三者的单元。"""

    def __init__(
        self,
        store: MemoryStore,
        embedder: EmbeddingClient,
        chunk_size: int = 1000,
        overlap: int = 200,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._chunk_size = chunk_size
        self._overlap = overlap

    async def add_texts(
        self, texts: list[str], collection: str, metadata: dict | None = None
    ) -> list[int]:
        all_chunks: list[str] = []
        for t in texts:
            all_chunks.extend(chunk(t, self._chunk_size, self._overlap))
        if not all_chunks:
            return []
        vectors = await self._embedder.embed(all_chunks)
        items = [(collection, c, metadata, v) for c, v in zip(all_chunks, vectors)]
        return self._store.add(items)

    async def search(self, query: str, collection: str, k: int) -> list[MemoryHit]:
        vectors = await self._embedder.embed([query])
        return self._store.search(collection, vectors[0], k)

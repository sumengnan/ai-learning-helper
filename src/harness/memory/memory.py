from __future__ import annotations

from ..memory.store import MemoryHit          # 保留旧返回形状，消费方零改动
from .backend import MemoryBackend
from .chunker import chunk
from .embeddings import EmbeddingClient
from .record import MemoryRecord, MemType


def collection_to_scope(collection: str) -> tuple[str, str]:
    """旧 collection 字符串 → (owner_id, kind)。"<kind>:<owner>" / "<kind>"（无 owner→_global）。"""
    if ":" in collection:
        kind, owner = collection.split(":", 1)
        return owner, kind
    return "_global", collection


class Memory:
    """串起 chunker + embedder + backend 的门面。保留 add_texts/search 兼容签名。"""

    def __init__(self, backend: MemoryBackend, embedder: EmbeddingClient,
                 chunk_size: int = 1000, overlap: int = 200) -> None:
        self._backend = backend
        self._embedder = embedder
        self._chunk_size = chunk_size
        self._overlap = overlap

    async def add_texts(self, texts: list[str], collection: str,
                        metadata: dict | None = None) -> list[str]:
        all_chunks: list[str] = []
        for t in texts:
            all_chunks.extend(chunk(t, self._chunk_size, self._overlap))
        if not all_chunks:
            return []
        owner_id, kind = collection_to_scope(collection)
        vectors = await self._embedder.embed(all_chunks)
        records = [
            MemoryRecord(owner_id=owner_id, kind=kind, mem_type=MemType.SEMANTIC,
                         text=c, embedding=v, metadata=metadata or {})
            for c, v in zip(all_chunks, vectors)]
        return self._backend.upsert(records)

    async def search(self, query: str, collection: str, k: int) -> list[MemoryHit]:
        from .record import MemoryFilter
        owner_id, kind = collection_to_scope(collection)
        vectors = await self._embedder.embed([query])
        hits = self._backend.vector_search(
            vectors[0], filters=MemoryFilter(owner_id=owner_id, kind=kind), k=k)
        return [MemoryHit(text=h.record.text, collection=collection,
                          metadata=h.record.metadata, distance=h.distance,
                          id=h.record.id, created_at=h.record.created_at)
                for h in hits]

# src/harness/memory/backend.py
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .record import MemoryFilter, MemoryHit, MemoryRecord


@runtime_checkable
class MemoryBackend(Protocol):
    """记忆存储可替换切点。SqliteVecBackend 实现之；日后 PgVector/Qdrant 实现同签名即可切换。"""

    def upsert(self, records: list[MemoryRecord]) -> list[str]:
        """按 id 存在则覆盖、不存在则插入，返回 id 列表。"""
        ...

    def delete(self, ids: list[str]) -> None: ...

    def get(self, ids: list[str]) -> list[MemoryRecord]: ...

    def vector_search(self, query_embedding: list[float], *,
                      filters: MemoryFilter, k: int) -> list[MemoryHit]:
        """向量 KNN，filters 下推进 KNN（分区键 + metadata 列），非查后过滤。"""
        ...

    def keyword_search(self, query_text: str, *,
                       filters: MemoryFilter, k: int) -> list[MemoryHit]:
        """SP1 占位返回空；FTS5 真实现留 SP2（签名先定死）。"""
        ...

    def get_embeddings(self, ids: list[str]) -> dict[str, list[float]]:
        """按记录 id 批量取回向量（MMR 用）。"""
        ...

    def list_by_entity(self, owner_id: str, kind: str,
                       entity_key: str) -> list[MemoryRecord]:
        """按实体键查同一实体的记录（SP3 upsert-by-entity 用）。"""
        ...

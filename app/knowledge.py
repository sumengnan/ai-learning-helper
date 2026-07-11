# app/knowledge.py
from __future__ import annotations

from uuid import uuid4

from .parsing import parse_file


class EmptyDocument(Exception):
    pass


class KnowledgeService:
    def __init__(self, memory, memory_store, doc_store, collection: str = "knowledge") -> None:
        self._memory = memory
        self._memory_store = memory_store
        self._doc_store = doc_store
        self._collection = collection

    def _collection_for(self, user_id: str) -> str:
        return f"{self._collection}:{user_id}"

    async def ingest(self, user_id: str, filename: str, data: bytes) -> dict:
        text = parse_file(filename, data)          # UnsupportedFormat 冒泡
        if not text.strip():
            raise EmptyDocument(filename)
        doc_id = uuid4().hex
        chunk_ids = await self._memory.add_texts(
            [text], self._collection_for(user_id),
            {"source": filename, "doc_id": doc_id, "user_id": user_id})
        excerpt = " ".join(text.split())[:200]     # 压平空白后取首段作摘要
        self._doc_store.create(user_id, doc_id, filename, len(data), chunk_ids, excerpt)
        return {"id": doc_id, "filename": filename, "num_chunks": len(chunk_ids)}

    async def search(self, user_id: str, query: str, k: int = 30) -> list[dict]:
        hits = await self._memory.search(query, self._collection_for(user_id), k)
        # 按 doc_id 聚合，取每文档最小 distance（越小越相关）的命中为代表
        best: dict[str, object] = {}
        for h in hits:
            doc_id = h.metadata.get("doc_id")
            if doc_id is None:
                continue
            cur = best.get(doc_id)
            if cur is None or h.distance < cur.distance:
                best[doc_id] = h
        results = []
        for doc_id, h in best.items():
            doc = self._doc_store.get(user_id, doc_id)
            if doc is None:                        # 文档已删、chunk 残留则跳过
                continue
            relevance = max(0, min(100, round(100 / (1 + h.distance))))
            results.append({
                "id": doc_id, "filename": doc["filename"], "uploaded_at": doc["uploaded_at"],
                "category": doc["category"], "excerpt": " ".join(h.text.split())[:200],
                "relevance": relevance,
            })
        results.sort(key=lambda r: r["relevance"], reverse=True)
        return results

    def delete(self, user_id: str, doc_id: str) -> None:
        self._memory_store.delete(self._doc_store.chunk_ids(user_id, doc_id))
        self._doc_store.delete(user_id, doc_id)

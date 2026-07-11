# app/knowledge.py
from __future__ import annotations

from uuid import uuid4

from .documents import _category
from .parsing import parse_file


def _clip(text: str, n: int = 300) -> str:
    return " ".join(text.split())[:n]


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

    def list_fragments(self, user_id: str, page: int = 1, size: int = 8) -> dict:
        """分页列举该用户知识库的所有片段（chunk），每片一项。"""
        kind = self._collection
        offset = (max(1, page) - 1) * size
        records = self._memory_store.list_by_owner(user_id, kind, limit=size, offset=offset)
        items = [self._fragment(r.id, r.text, r.metadata, r.created_at) for r in records]
        return {"items": items, "total": self._memory_store.count_by_owner(user_id, kind)}

    async def search(self, user_id: str, query: str, k: int = 30) -> list[dict]:
        """片段级语义检索：每个命中 chunk 一项，带相关度。"""
        hits = await self._memory.search(query, self._collection_for(user_id), k)
        results = []
        for h in hits:
            # distance = 1 - 融合分（越小越相关，可能为负）；相关度 = 融合分裁剪到 0–100%
            relevance = max(0, min(100, round((1 - h.distance) * 100)))
            item = self._fragment(h.id, h.text, h.metadata, h.created_at)
            item["relevance"] = relevance
            results.append(item)
        results.sort(key=lambda r: r["relevance"], reverse=True)
        return results

    @staticmethod
    def _fragment(chunk_id: str, text: str, metadata: dict, created_at: str) -> dict:
        filename = (metadata or {}).get("source", "")
        return {"id": chunk_id, "filename": filename, "excerpt": _clip(text),
                "category": _category(filename), "uploaded_at": created_at}

    def delete_fragment(self, user_id: str, chunk_id: str) -> bool:
        """删除单个片段：从向量库移除并回写父文档 chunk_ids/num_chunks。找不到返回 False。"""
        recs = self._memory_store.get([chunk_id])
        if not recs or recs[0].owner_id != user_id:
            return False
        doc_id = (recs[0].metadata or {}).get("doc_id")
        self._memory_store.delete([chunk_id])
        if doc_id:
            self._doc_store.remove_chunk(user_id, doc_id, chunk_id)
        return True

    def delete(self, user_id: str, doc_id: str) -> None:
        self._memory_store.delete(self._doc_store.chunk_ids(user_id, doc_id))
        self._doc_store.delete(user_id, doc_id)

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

    async def ingest(self, filename: str, data: bytes) -> dict:
        text = parse_file(filename, data)          # UnsupportedFormat 冒泡
        if not text.strip():
            raise EmptyDocument(filename)
        doc_id = uuid4().hex
        chunk_ids = await self._memory.add_texts(
            [text], self._collection, {"source": filename, "doc_id": doc_id})
        self._doc_store.create(doc_id, filename, len(data), chunk_ids)
        return {"id": doc_id, "filename": filename, "num_chunks": len(chunk_ids)}

    def delete(self, doc_id: str) -> None:
        self._memory_store.delete(self._doc_store.chunk_ids(doc_id))
        self._doc_store.delete(doc_id)

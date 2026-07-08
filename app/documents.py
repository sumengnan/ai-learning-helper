# app/documents.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DocumentStore:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS documents("
            "id TEXT PRIMARY KEY, filename TEXT, size INTEGER, num_chunks INTEGER, "
            "chunk_ids TEXT, uploaded_at TEXT)")
        self._conn.commit()

    def create(self, doc_id: str, filename: str, size: int, chunk_ids: list[int]) -> None:
        self._conn.execute(
            "INSERT INTO documents(id, filename, size, num_chunks, chunk_ids, uploaded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (doc_id, filename, size, len(chunk_ids), json.dumps(chunk_ids), _now()))
        self._conn.commit()

    def list(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, filename, size, num_chunks, uploaded_at FROM documents "
            "ORDER BY uploaded_at DESC").fetchall()
        return [{"id": r[0], "filename": r[1], "size": r[2],
                 "num_chunks": r[3], "uploaded_at": r[4]} for r in rows]

    def exists(self, doc_id: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM documents WHERE id = ?", (doc_id,)).fetchone() is not None

    def chunk_ids(self, doc_id: str) -> list[int]:
        row = self._conn.execute(
            "SELECT chunk_ids FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return json.loads(row[0]) if row else []

    def delete(self, doc_id: str) -> None:
        self._conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        self._conn.commit()

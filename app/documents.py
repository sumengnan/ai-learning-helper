# app/documents.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .db import migrate, open_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_CATEGORY = {"pdf": "PDF", "docx": "Word", "doc": "Word",
             "txt": "文本", "md": "Markdown"}


def _category(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _CATEGORY.get(ext, "其他")


class DocumentStore:
    def __init__(self, db_path: str | None = None, *,
                 conn: sqlite3.Connection | None = None) -> None:
        if conn is not None:
            self._conn = conn
        else:
            self._conn = open_db(db_path)
            migrate(self._conn)

    def create(self, user_id: str, doc_id: str, filename: str, size: int,
               chunk_ids: list[str], excerpt: str = "") -> None:
        self._conn.execute(
            "INSERT INTO documents(id, user_id, filename, size, num_chunks, chunk_ids, uploaded_at, excerpt) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (doc_id, user_id, filename, size, len(chunk_ids), json.dumps(chunk_ids), _now(), excerpt))
        self._conn.commit()

    def list(self, user_id: str, limit: int | None = None, offset: int = 0) -> list[dict]:
        sql = ("SELECT id, filename, size, num_chunks, uploaded_at, excerpt FROM documents "
               "WHERE user_id = ? ORDER BY uploaded_at DESC")
        params: list = [user_id]
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params += [limit, offset]
        rows = self._conn.execute(sql, params).fetchall()
        return [{"id": r[0], "filename": r[1], "size": r[2], "num_chunks": r[3],
                 "uploaded_at": r[4], "excerpt": r[5] or "", "category": _category(r[1])}
                for r in rows]

    def count(self, user_id: str) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM documents WHERE user_id = ?", (user_id,)).fetchone()[0]

    def total_chunks(self, user_id: str) -> int:
        return self._conn.execute(
            "SELECT COALESCE(SUM(num_chunks), 0) FROM documents WHERE user_id = ?",
            (user_id,)).fetchone()[0]

    def get(self, user_id: str, doc_id: str) -> dict | None:
        r = self._conn.execute(
            "SELECT id, filename, size, num_chunks, uploaded_at, excerpt FROM documents "
            "WHERE id = ? AND user_id = ?", (doc_id, user_id)).fetchone()
        if r is None:
            return None
        return {"id": r[0], "filename": r[1], "size": r[2], "num_chunks": r[3],
                "uploaded_at": r[4], "excerpt": r[5] or "", "category": _category(r[1])}

    def exists(self, user_id: str, doc_id: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM documents WHERE id = ? AND user_id = ?",
            (doc_id, user_id)).fetchone() is not None

    def chunk_ids(self, user_id: str, doc_id: str) -> list[str]:
        row = self._conn.execute(
            "SELECT chunk_ids FROM documents WHERE id = ? AND user_id = ?",
            (doc_id, user_id)).fetchone()
        return json.loads(row[0]) if row else []

    def delete(self, user_id: str, doc_id: str) -> None:
        self._conn.execute("DELETE FROM documents WHERE id = ? AND user_id = ?",
                           (doc_id, user_id))
        self._conn.commit()

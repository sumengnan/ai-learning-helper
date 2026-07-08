from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

import sqlite_vec


@dataclass
class MemoryHit:
    text: str
    collection: str
    metadata: dict
    distance: float


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    """sqlite-vec 向量存储。向量表 rowid 与元数据表 id 对齐。"""

    def __init__(self, db_path: str, dimension: int) -> None:
        self._dim = dimension
        self._conn = sqlite3.connect(db_path)
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)
        self._conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS memory_vectors "
            f"USING vec0(embedding float[{dimension}])"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS memory_items("
            "id INTEGER PRIMARY KEY, collection TEXT NOT NULL, text TEXT NOT NULL, "
            "metadata TEXT, created_at TEXT NOT NULL)"
        )
        self._conn.commit()

    def add(self, items: list[tuple[str, str, dict, list[float]]]) -> list[int]:
        ids: list[int] = []
        for collection, text, metadata, embedding in items:
            cur = self._conn.execute(
                "INSERT INTO memory_items(collection, text, metadata, created_at) "
                "VALUES (?, ?, ?, ?)",
                (collection, text, json.dumps(metadata or {}), _now()),
            )
            rowid = cur.lastrowid
            self._conn.execute(
                "INSERT INTO memory_vectors(rowid, embedding) VALUES (?, ?)",
                (rowid, sqlite_vec.serialize_float32(embedding)),
            )
            ids.append(rowid)
        self._conn.commit()
        return ids

    def search(self, collection: str, query_embedding: list[float], k: int) -> list[MemoryHit]:
        over = k * 4  # over-fetch 后按 collection 过滤
        rows = self._conn.execute(
            "SELECT rowid, distance FROM memory_vectors "
            "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (sqlite_vec.serialize_float32(query_embedding), over),
        ).fetchall()
        hits: list[MemoryHit] = []
        for rowid, distance in rows:
            item = self._conn.execute(
                "SELECT collection, text, metadata FROM memory_items WHERE id = ?",
                (rowid,),
            ).fetchone()
            if item is None:
                continue
            coll, text, metadata = item
            if coll != collection:
                continue
            hits.append(MemoryHit(
                text=text, collection=coll,
                metadata=json.loads(metadata or "{}"), distance=distance))
            if len(hits) >= k:
                break
        return hits

    def delete(self, ids: list[int]) -> None:
        for i in ids:
            self._conn.execute("DELETE FROM memory_items WHERE id = ?", (i,))
            self._conn.execute("DELETE FROM memory_vectors WHERE rowid = ?", (i,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

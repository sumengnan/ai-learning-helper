# app/downloads.py
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DownloadStore:
    def __init__(self, files_dir: str, db_path: str) -> None:
        self._dir = files_dir
        os.makedirs(files_dir, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS downloads(
                 id TEXT PRIMARY KEY, user_id TEXT, filename TEXT, size INTEGER,
                 content_type TEXT, created_at TEXT, seq INTEGER)""")
        self._db.commit()
        self._seq = self._db.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM downloads").fetchone()[0]

    def create(self, user_id: str | None, filename: str, data: bytes, content_type: str) -> dict:
        did = uuid4().hex
        with open(os.path.join(self._dir, did), "wb") as f:
            f.write(data)
        self._seq += 1
        self._db.execute(
            "INSERT INTO downloads(id, user_id, filename, size, content_type, created_at, seq) "
            "VALUES (?,?,?,?,?,?,?)",
            (did, user_id, filename, len(data), content_type, _now(), self._seq))
        self._db.commit()
        return {"id": did, "filename": filename, "size": len(data),
                "content_type": content_type}

    def _row(self, r) -> dict:
        return {"id": r[0], "filename": r[1], "size": r[2],
                "content_type": r[3], "created_at": r[4]}

    def list(self, user_id: str | None) -> list[dict]:
        rows = self._db.execute(
            "SELECT id,filename,size,content_type,created_at "
            "FROM downloads WHERE user_id=? ORDER BY seq DESC", (user_id,)).fetchall()
        return [self._row(r) for r in rows]

    def get(self, user_id: str | None, did: str) -> dict | None:
        r = self._db.execute(
            "SELECT id,filename,size,content_type,created_at "
            "FROM downloads WHERE id=? AND user_id=?", (did, user_id)).fetchone()
        if not r:
            return None
        d = self._row(r)
        d["path"] = self.path(did)
        return d

    def path(self, did: str) -> str:
        return os.path.join(self._dir, did)

    def delete(self, user_id: str | None, did: str) -> bool:
        r = self._db.execute(
            "SELECT id FROM downloads WHERE id=? AND user_id=?", (did, user_id)).fetchone()
        if not r:
            return False
        p = self.path(did)
        if os.path.exists(p):
            os.remove(p)
        self._db.execute("DELETE FROM downloads WHERE id=?", (did,))
        self._db.commit()
        return True

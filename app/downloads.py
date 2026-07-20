# app/downloads.py
from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from .db import migrate, open_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DownloadStore:
    def __init__(self, files_dir: str, db_path: str | None = None, *,
                 conn: sqlite3.Connection | None = None) -> None:
        self._dir = files_dir
        os.makedirs(files_dir, exist_ok=True)
        if conn is not None:
            self._db = conn
        else:
            self._db = open_db(db_path)
            migrate(self._db)
        self._seq = self._db.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM downloads").fetchone()[0]

    def create(self, user_id: str | None, filename: str, data: bytes, content_type: str) -> dict:
        """存一份可下载文件。同名同内容已存在则复用旧记录，不重复落盘。

        去重的直接动因：编排器的单步重试会把带副作用的工具原样再调一遍（max_step_retry=2
        即初次 + 1 次重试），同一份笔记于是被存两次，消息下方冒出两个一模一样的下载按钮。
        按 (user, filename, sha256) 判重——内容变了就是新文件，不会误合并。
        """
        h = hashlib.sha256(data).hexdigest()
        row = self._db.execute(
            "SELECT id, filename, size, content_type FROM downloads "
            "WHERE user_id IS ? AND filename=? AND content_hash=?",
            (user_id, filename, h)).fetchone()
        if row is not None:
            return {"id": row[0], "filename": row[1], "size": row[2], "content_type": row[3]}
        did = uuid4().hex
        with open(os.path.join(self._dir, did), "wb") as f:
            f.write(data)
        self._seq += 1
        self._db.execute(
            "INSERT INTO downloads(id, user_id, filename, size, content_type, created_at, "
            "seq, content_hash) VALUES (?,?,?,?,?,?,?,?)",
            (did, user_id, filename, len(data), content_type, _now(), self._seq, h))
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

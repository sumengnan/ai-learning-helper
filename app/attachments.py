# app/attachments.py
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from .db import migrate, open_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AttachmentStore:
    """聊天附件：裸字节落盘（attachments_dir/<id>），元数据入 attachments 表。

    结构照搬 DownloadStore，但附件归属「会话」（conv_id）而非仅用户：预览/沙箱/视觉
    都要裸字节，发送后经 conversation_messages.attachments 列与具体消息关联。
    """

    def __init__(self, files_dir: str, db_path: str | None = None, *,
                 conn: sqlite3.Connection | None = None) -> None:
        self._dir = files_dir
        os.makedirs(files_dir, exist_ok=True)
        if conn is not None:
            self._db = conn
        else:
            self._db = open_db(db_path)
            migrate(self._db)

    def create(self, user_id: str, conv_id: str, filename: str, data: bytes,
               content_type: str) -> dict:
        aid = uuid4().hex
        with open(os.path.join(self._dir, aid), "wb") as f:
            f.write(data)
        self._db.execute(
            "INSERT INTO attachments(id, user_id, conv_id, filename, size, content_type, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (aid, user_id, conv_id, filename, len(data), content_type, _now()))
        self._db.commit()
        return {"id": aid, "filename": filename, "size": len(data),
                "content_type": content_type}

    def _meta(self, r) -> dict:
        return {"id": r[0], "filename": r[1], "size": r[2], "content_type": r[3]}

    def get(self, user_id: str, aid: str) -> dict | None:
        r = self._db.execute(
            "SELECT id, filename, size, content_type FROM attachments "
            "WHERE id=? AND user_id=?", (aid, user_id)).fetchone()
        if not r:
            return None
        d = self._meta(r)
        d["path"] = self.path(aid)
        return d

    def bytes(self, aid: str) -> bytes:
        with open(self.path(aid), "rb") as f:
            return f.read()

    def path(self, aid: str) -> str:
        return os.path.join(self._dir, aid)

    def list_conv(self, user_id: str, conv_id: str) -> list[dict]:
        rows = self._db.execute(
            "SELECT id, filename, size, content_type FROM attachments "
            "WHERE user_id=? AND conv_id=? ORDER BY created_at", (user_id, conv_id)).fetchall()
        return [self._meta(r) for r in rows]

    def count_conv(self, user_id: str, conv_id: str) -> int:
        return self._db.execute(
            "SELECT COUNT(*) FROM attachments WHERE user_id=? AND conv_id=?",
            (user_id, conv_id)).fetchone()[0]

    def delete(self, user_id: str, aid: str) -> bool:
        r = self._db.execute(
            "SELECT id FROM attachments WHERE id=? AND user_id=?", (aid, user_id)).fetchone()
        if not r:
            return False
        p = self.path(aid)
        if os.path.exists(p):
            os.remove(p)
        self._db.execute("DELETE FROM attachments WHERE id=?", (aid,))
        self._db.commit()
        return True

    def delete_conv(self, conv_id: str) -> None:
        """删除会话时清理其全部附件（文件 + 行）。"""
        rows = self._db.execute(
            "SELECT id FROM attachments WHERE conv_id=?", (conv_id,)).fetchall()
        for (aid,) in rows:
            p = self.path(aid)
            if os.path.exists(p):
                os.remove(p)
        self._db.execute("DELETE FROM attachments WHERE conv_id=?", (conv_id,))
        self._db.commit()

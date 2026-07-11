# app/summaries.py
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from .db import migrate, open_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SummaryRecord:
    up_to_seq: int          # 水位：已被摘要覆盖的历史消息前缀长度
    summary: str
    tokens: int


class SummaryStore:
    """L2 滚动摘要的持久化（conversation_summaries 表，每会话一行）。"""

    def __init__(self, db_path: str | None = None, *,
                 conn: sqlite3.Connection | None = None) -> None:
        if conn is not None:
            self._conn = conn
        else:
            self._conn = open_db(db_path)
            migrate(self._conn)

    def get(self, conv_id: str) -> SummaryRecord | None:
        row = self._conn.execute(
            "SELECT up_to_seq, summary, tokens FROM conversation_summaries WHERE conv_id = ?",
            (conv_id,)).fetchone()
        return SummaryRecord(row[0], row[1], row[2]) if row else None

    def upsert(self, conv_id: str, up_to_seq: int, summary: str, tokens: int) -> None:
        self._conn.execute(
            "INSERT INTO conversation_summaries(conv_id, up_to_seq, summary, tokens, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(conv_id) DO UPDATE SET "
            "up_to_seq=excluded.up_to_seq, summary=excluded.summary, "
            "tokens=excluded.tokens, created_at=excluded.created_at",
            (conv_id, up_to_seq, summary, tokens, _now()))
        self._conn.commit()

    def delete(self, conv_id: str) -> None:
        self._conn.execute(
            "DELETE FROM conversation_summaries WHERE conv_id = ?", (conv_id,))
        self._conn.commit()

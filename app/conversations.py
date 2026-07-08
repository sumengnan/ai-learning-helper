# app/conversations.py
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from harness.persistence.serialize import message_from_dict, message_to_dict
from harness.types import Message


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationStore:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS conversations("
            "id TEXT PRIMARY KEY, title TEXT, created_at TEXT)")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS conversation_messages("
            "conv_id TEXT, seq INTEGER, role TEXT, content TEXT, tool_calls TEXT, "
            "tool_call_id TEXT, created_at TEXT, PRIMARY KEY(conv_id, seq))")
        self._conn.commit()

    def create(self, title: str = "新对话") -> str:
        cid = uuid.uuid4().hex
        self._conn.execute("INSERT INTO conversations(id, title, created_at) VALUES (?, ?, ?)",
                           (cid, title, _now()))
        self._conn.commit()
        return cid

    def list(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, title, created_at FROM conversations ORDER BY created_at DESC").fetchall()
        return [{"id": r[0], "title": r[1], "created_at": r[2]} for r in rows]

    def exists(self, conv_id: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM conversations WHERE id = ?", (conv_id,)).fetchone() is not None

    def messages(self, conv_id: str) -> list[Message]:
        rows = self._conn.execute(
            "SELECT role, content, tool_calls, tool_call_id FROM conversation_messages "
            "WHERE conv_id = ? ORDER BY seq", (conv_id,)).fetchall()
        out: list[Message] = []
        for role, content, tool_calls, tool_call_id in rows:
            out.append(message_from_dict({
                "role": role, "content": content,
                "tool_calls": json.loads(tool_calls) if tool_calls else [],
                "tool_call_id": tool_call_id,
            }))
        return out

    def append(self, conv_id: str, msgs: list[Message]) -> None:
        seq = self._conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 FROM conversation_messages WHERE conv_id = ?",
            (conv_id,)).fetchone()[0]
        for m in msgs:
            d = message_to_dict(m)
            self._conn.execute(
                "INSERT INTO conversation_messages(conv_id, seq, role, content, tool_calls, "
                "tool_call_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (conv_id, seq, d["role"], d["content"],
                 json.dumps(d["tool_calls"], ensure_ascii=False) if d["tool_calls"] else None,
                 d["tool_call_id"], _now()))
            seq += 1
        self._conn.commit()

    def delete(self, conv_id: str) -> None:
        self._conn.execute("DELETE FROM conversation_messages WHERE conv_id = ?", (conv_id,))
        self._conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        self._conn.commit()

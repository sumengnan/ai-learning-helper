# app/conversations.py
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from harness.persistence.serialize import message_from_dict, message_to_dict
from harness.types import Message

from .db import migrate, open_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationStore:
    def __init__(self, db_path: str | None = None, *,
                 conn: sqlite3.Connection | None = None) -> None:
        if conn is not None:
            self._conn = conn
        else:
            self._conn = open_db(db_path)
            migrate(self._conn)

    def create(self, user_id: str, title: str = "新对话") -> str:
        cid = uuid.uuid4().hex
        self._conn.execute(
            "INSERT INTO conversations(id, user_id, title, created_at) VALUES (?, ?, ?, ?)",
            (cid, user_id, title, _now()))
        self._conn.commit()
        return cid

    def list(self, user_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, title, created_at FROM conversations WHERE user_id = ? "
            "ORDER BY created_at DESC", (user_id,)).fetchall()
        return [{"id": r[0], "title": r[1], "created_at": r[2]} for r in rows]

    def exists(self, user_id: str, conv_id: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM conversations WHERE id = ? AND user_id = ?",
            (conv_id, user_id)).fetchone() is not None

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

    def append(self, conv_id: str, msgs: list[Message],
               steps: list[dict] | None = None) -> None:
        """追加消息。steps 为纯 UI 用途的工具调用轨迹（tool/args/result/is_error），
        挂在本批最后一条（助手）消息上，不参与 messages() 返回的 LLM 历史。"""
        seq = self._conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 FROM conversation_messages WHERE conv_id = ?",
            (conv_id,)).fetchone()[0]
        last = len(msgs) - 1
        for i, m in enumerate(msgs):
            d = message_to_dict(m)
            steps_json = (json.dumps(steps, ensure_ascii=False)
                          if steps and i == last else None)
            self._conn.execute(
                "INSERT INTO conversation_messages(conv_id, seq, role, content, tool_calls, "
                "tool_call_id, steps, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (conv_id, seq, d["role"], d["content"],
                 json.dumps(d["tool_calls"], ensure_ascii=False) if d["tool_calls"] else None,
                 d["tool_call_id"], steps_json, _now()))
            seq += 1
        self._conn.commit()

    def ui_messages(self, conv_id: str) -> list[dict]:
        """供前端渲染：role + content + steps（含工具调用轨迹）。"""
        rows = self._conn.execute(
            "SELECT role, content, steps FROM conversation_messages "
            "WHERE conv_id = ? ORDER BY seq", (conv_id,)).fetchall()
        return [{"role": role, "content": content,
                 "steps": json.loads(steps) if steps else None}
                for role, content, steps in rows]

    def rename(self, user_id: str, conv_id: str, title: str) -> bool:
        cur = self._conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ? AND user_id = ?",
            (title, conv_id, user_id))
        self._conn.commit()
        return cur.rowcount > 0

    def delete(self, user_id: str, conv_id: str) -> None:
        if not self.exists(user_id, conv_id):
            return
        self._conn.execute("DELETE FROM conversation_messages WHERE conv_id = ?", (conv_id,))
        self._conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        self._conn.commit()

# app/questions.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from .db import migrate, open_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class QuestionStore:
    def __init__(self, db_path: str | None = None, *,
                 conn: sqlite3.Connection | None = None) -> None:
        if conn is not None:
            self._db = conn
        else:
            self._db = open_db(db_path)
            migrate(self._db)

    _COLS = "id, type, stem, options, answer, explanation, source, created_at"

    def create(self, user_id: str, q: dict) -> str:
        qid = uuid4().hex
        self._db.execute(
            "INSERT INTO questions(id, user_id, type, stem, options, answer, explanation, "
            "source, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (qid, user_id, q["type"], q["stem"],
             json.dumps(q.get("options"), ensure_ascii=False),
             json.dumps(q.get("answer"), ensure_ascii=False),
             q.get("explanation", ""), q.get("source", ""), _now()))
        self._db.commit()
        return qid

    def create_deduped(self, user_id: str, q: dict) -> str | None:
        """按 (type, TRIM(stem)) 判重：已存在同题型同题干则返回 None 不插入，否则新建返回 id。"""
        row = self._db.execute(
            "SELECT id FROM questions WHERE user_id=? AND type=? AND TRIM(stem)=TRIM(?)",
            (user_id, q["type"], q["stem"])).fetchone()
        if row is not None:
            return None
        return self.create(user_id, q)

    def _row(self, r) -> dict:
        return {"id": r[0], "type": r[1], "stem": r[2],
                "options": json.loads(r[3]), "answer": json.loads(r[4]),
                "explanation": r[5], "source": r[6], "created_at": r[7]}

    def get(self, user_id: str, qid: str) -> dict | None:
        r = self._db.execute(
            f"SELECT {self._COLS} FROM questions WHERE id=? AND user_id=?",
            (qid, user_id)).fetchone()
        return self._row(r) if r else None

    def list(self, user_id: str) -> list[dict]:
        rows = self._db.execute(
            f"SELECT {self._COLS} FROM questions WHERE user_id=? ORDER BY created_at",
            (user_id,)).fetchall()
        return [self._row(r) for r in rows]

    def sample(self, user_id: str, count: int, types: list[str] | None) -> list[dict]:
        if types:
            ph = ",".join("?" * len(types))
            rows = self._db.execute(
                f"SELECT {self._COLS} FROM questions WHERE user_id=? AND type IN ({ph}) "
                "ORDER BY RANDOM() LIMIT ?", (user_id, *types, count)).fetchall()
        else:
            rows = self._db.execute(
                f"SELECT {self._COLS} FROM questions WHERE user_id=? ORDER BY RANDOM() LIMIT ?",
                (user_id, count)).fetchall()
        return [self._row(r) for r in rows]

    def delete(self, user_id: str, qid: str) -> None:
        self._db.execute("DELETE FROM questions WHERE id=? AND user_id=?", (qid, user_id))
        self._db.commit()

    def delete_many(self, user_id: str, ids: list[str]) -> None:
        self._db.executemany("DELETE FROM questions WHERE id=? AND user_id=?",
                             [(i, user_id) for i in ids])
        self._db.commit()

# app/questions.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class QuestionStore:
    def __init__(self, db_path: str) -> None:
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS questions(
                 id TEXT PRIMARY KEY, type TEXT, stem TEXT, options TEXT,
                 answer TEXT, explanation TEXT, source TEXT, created_at TEXT)""")
        self._db.commit()

    def create(self, q: dict) -> str:
        qid = uuid4().hex
        self._db.execute(
            "INSERT INTO questions VALUES (?,?,?,?,?,?,?,?)",
            (qid, q["type"], q["stem"],
             json.dumps(q.get("options"), ensure_ascii=False),
             json.dumps(q.get("answer"), ensure_ascii=False),
             q.get("explanation", ""), q.get("source", ""), _now()))
        self._db.commit()
        return qid

    def _row(self, r) -> dict:
        return {"id": r[0], "type": r[1], "stem": r[2],
                "options": json.loads(r[3]), "answer": json.loads(r[4]),
                "explanation": r[5], "source": r[6], "created_at": r[7]}

    def get(self, qid: str) -> dict | None:
        r = self._db.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
        return self._row(r) if r else None

    def list(self) -> list[dict]:
        rows = self._db.execute("SELECT * FROM questions ORDER BY created_at").fetchall()
        return [self._row(r) for r in rows]

    def sample(self, count: int, types: list[str] | None) -> list[dict]:
        if types:
            ph = ",".join("?" * len(types))
            rows = self._db.execute(
                f"SELECT * FROM questions WHERE type IN ({ph}) ORDER BY RANDOM() LIMIT ?",
                (*types, count)).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM questions ORDER BY RANDOM() LIMIT ?", (count,)).fetchall()
        return [self._row(r) for r in rows]

    def delete(self, qid: str) -> None:
        self._db.execute("DELETE FROM questions WHERE id=?", (qid,))
        self._db.commit()

    def delete_many(self, ids: list[str]) -> None:
        self._db.executemany("DELETE FROM questions WHERE id=?", [(i,) for i in ids])
        self._db.commit()

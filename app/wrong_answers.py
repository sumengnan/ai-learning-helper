# app/wrong_answers.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WrongAnswerStore:
    def __init__(self, db_path: str) -> None:
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS wrong_answers(
                 id TEXT PRIMARY KEY, question_id TEXT, exam_id TEXT,
                 snapshot TEXT, user_answer TEXT, created_at TEXT, seq INTEGER)""")
        self._db.commit()
        self._seq = self._db.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM wrong_answers").fetchone()[0]

    def create(self, question_id: str, exam_id: str, snapshot: dict, user_answer) -> str:
        wid = uuid4().hex
        self._seq += 1
        self._db.execute(
            "INSERT INTO wrong_answers VALUES (?,?,?,?,?,?,?)",
            (wid, question_id, exam_id,
             json.dumps(snapshot, ensure_ascii=False),
             json.dumps(user_answer, ensure_ascii=False), _now(), self._seq))
        self._db.commit()
        return wid

    def list(self) -> list[dict]:
        rows = self._db.execute(
            "SELECT id,question_id,exam_id,snapshot,user_answer,created_at "
            "FROM wrong_answers ORDER BY seq DESC").fetchall()
        return [{"id": r[0], "question_id": r[1], "exam_id": r[2],
                 "snapshot": json.loads(r[3]), "user_answer": json.loads(r[4]),
                 "created_at": r[5]} for r in rows]

    def delete(self, wid: str) -> None:
        self._db.execute("DELETE FROM wrong_answers WHERE id=?", (wid,))
        self._db.commit()

    def delete_many(self, ids: list[str]) -> None:
        self._db.executemany("DELETE FROM wrong_answers WHERE id=?", [(i,) for i in ids])
        self._db.commit()

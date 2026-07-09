# app/exams.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExamStore:
    def __init__(self, db_path: str) -> None:
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS exam_results(
                 id TEXT PRIMARY KEY, user_id TEXT, created_at TEXT, seq INTEGER,
                 total INTEGER, correct INTEGER, score REAL, detail TEXT)""")
        self._db.commit()
        self._seq = self._db.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM exam_results").fetchone()[0]

    def create(self, user_id: str, total: int, correct: int, score: float, detail: list) -> str:
        eid = uuid4().hex
        self._seq += 1
        self._db.execute(
            "INSERT INTO exam_results(id, user_id, created_at, seq, total, correct, score, detail) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (eid, user_id, _now(), self._seq, total, correct, score,
             json.dumps(detail, ensure_ascii=False)))
        self._db.commit()
        return eid

    def list(self, user_id: str) -> list[dict]:
        rows = self._db.execute(
            "SELECT id,created_at,total,correct,score,detail "
            "FROM exam_results WHERE user_id=? ORDER BY seq DESC", (user_id,)).fetchall()
        return [{"id": r[0], "created_at": r[1], "total": r[2], "correct": r[3],
                 "score": r[4], "detail": json.loads(r[5])} for r in rows]

# app/wrong_answers.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from .db import migrate, open_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WrongAnswerStore:
    def __init__(self, db_path: str | None = None, *,
                 conn: sqlite3.Connection | None = None) -> None:
        if conn is not None:
            self._db = conn
        else:
            self._db = open_db(db_path)
            migrate(self._db)
        self._seq = self._db.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM wrong_answers").fetchone()[0]

    def create(self, user_id: str, question_id: str, exam_id: str,
               snapshot: dict, user_answer) -> str:
        wid = uuid4().hex
        self._seq += 1
        self._db.execute(
            "INSERT INTO wrong_answers(id, user_id, question_id, exam_id, snapshot, "
            "user_answer, created_at, seq) VALUES (?,?,?,?,?,?,?,?)",
            (wid, user_id, question_id, exam_id,
             json.dumps(snapshot, ensure_ascii=False),
             json.dumps(user_answer, ensure_ascii=False), _now(), self._seq))
        self._db.commit()
        return wid

    _COLS = "id,question_id,exam_id,snapshot,user_answer,created_at"

    def _row(self, r) -> dict:
        return {"id": r[0], "question_id": r[1], "exam_id": r[2],
                "snapshot": json.loads(r[3]), "user_answer": json.loads(r[4]),
                "created_at": r[5]}

    def list(self, user_id: str) -> list[dict]:
        rows = self._db.execute(
            f"SELECT {self._COLS} FROM wrong_answers WHERE user_id=? ORDER BY seq DESC",
            (user_id,)).fetchall()
        return [self._row(r) for r in rows]

    def sample(self, user_id: str, count: int) -> list[dict]:
        rows = self._db.execute(
            f"SELECT {self._COLS} FROM wrong_answers WHERE user_id=? "
            "ORDER BY RANDOM() LIMIT ?", (user_id, count)).fetchall()
        return [self._row(r) for r in rows]

    def count_by_question(self, user_id: str, question_ids: list[str]) -> int:
        """统计属于给定题目的错题数（用于删题前提示会连带删掉多少条错题）。"""
        if not question_ids:
            return 0
        ph = ",".join("?" * len(question_ids))
        return self._db.execute(
            f"SELECT COUNT(*) FROM wrong_answers WHERE user_id=? AND question_id IN ({ph})",
            (user_id, *question_ids)).fetchone()[0]

    def delete_by_question(self, user_id: str, question_ids: list[str]) -> int:
        """删除属于给定题目的错题，返回删除条数。"""
        if not question_ids:
            return 0
        ph = ",".join("?" * len(question_ids))
        cur = self._db.execute(
            f"DELETE FROM wrong_answers WHERE user_id=? AND question_id IN ({ph})",
            (user_id, *question_ids))
        self._db.commit()
        return cur.rowcount

    def delete(self, user_id: str, wid: str) -> None:
        self._db.execute("DELETE FROM wrong_answers WHERE id=? AND user_id=?", (wid, user_id))
        self._db.commit()

    def delete_many(self, user_id: str, ids: list[str]) -> None:
        self._db.executemany("DELETE FROM wrong_answers WHERE id=? AND user_id=?",
                             [(i, user_id) for i in ids])
        self._db.commit()

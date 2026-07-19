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

    def _find_duplicate(self, user_id: str, snapshot: dict) -> str | None:
        """找同一道题已有的错题：按 (题型, 去空白题干) 判重——与题库 create_deduped 的判重口径
        一致（题干即"哪道题"，题库题与即席题统一按题干认）。找到返回其 id，否则 None。"""
        r = self._db.execute(
            "SELECT id FROM wrong_answers WHERE user_id=? "
            "AND json_extract(snapshot,'$.type')=? "
            "AND TRIM(json_extract(snapshot,'$.stem'))=TRIM(?)",
            (user_id, snapshot.get("type"), snapshot.get("stem", ""))).fetchone()
        return r[0] if r else None

    def create(self, user_id: str, question_id: str, exam_id: str,
               snapshot: dict, user_answer) -> str:
        """存入错题集，同题去重：已有同一道题（题型+题干相同）的错题则用新数据（question_id/
        来源/快照/作答）整条替换旧的，并刷新时间与 seq 让它回到列表顶部；否则新建。
        返回该错题 id（替换时为原 id）。"""
        snap = json.dumps(snapshot, ensure_ascii=False)
        ans = json.dumps(user_answer, ensure_ascii=False)
        self._seq += 1
        dup = self._find_duplicate(user_id, snapshot)
        if dup is not None:
            self._db.execute(
                "UPDATE wrong_answers SET question_id=?, exam_id=?, snapshot=?, user_answer=?, "
                "created_at=?, seq=? WHERE id=? AND user_id=?",
                (question_id, exam_id, snap, ans, _now(), self._seq, dup, user_id))
            self._db.commit()
            return dup
        wid = uuid4().hex
        self._db.execute(
            "INSERT INTO wrong_answers(id, user_id, question_id, exam_id, snapshot, "
            "user_answer, created_at, seq) VALUES (?,?,?,?,?,?,?,?)",
            (wid, user_id, question_id, exam_id, snap, ans, _now(), self._seq))
        self._db.commit()
        return wid

    _COLS = "id,question_id,exam_id,snapshot,user_answer,created_at"

    def _row(self, r) -> dict:
        return {"id": r[0], "question_id": r[1], "exam_id": r[2],
                "snapshot": json.loads(r[3]), "user_answer": json.loads(r[4]),
                "created_at": r[5]}

    def _filter(self, user_id: str, type, q):
        """题型/题干筛选。两者都存在 snapshot 的 JSON 里，故用 json_extract 取字段。"""
        clauses = ["user_id=?"]
        params: list = [user_id]
        if type:
            clauses.append("json_extract(snapshot, '$.type')=?"); params.append(type)
        if q:
            clauses.append("json_extract(snapshot, '$.stem') LIKE ?"); params.append(f"%{q}%")
        return " AND ".join(clauses), params

    def list(self, user_id: str, *, type: str | None = None, q: str | None = None,
             limit: int | None = None, offset: int = 0) -> list[dict]:
        where, params = self._filter(user_id, type, q)
        sql = f"SELECT {self._COLS} FROM wrong_answers WHERE {where} ORDER BY seq DESC"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"; params += [limit, offset]
        return [self._row(r) for r in self._db.execute(sql, params).fetchall()]

    def count(self, user_id: str, *, type: str | None = None, q: str | None = None) -> int:
        where, params = self._filter(user_id, type, q)
        return self._db.execute(
            f"SELECT COUNT(*) FROM wrong_answers WHERE {where}", params).fetchone()[0]

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

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

    def existing_dedup_keys(self, user_id: str) -> set[tuple[str, str]]:
        """一次取出该用户全部 (type, 去空白 stem) 判重键，供批量导入时在内存里判重。

        取代逐题 create_deduped 的「每题一次 SELECT」——那条 WHERE 里 TRIM(stem) 作用在
        列上，使任何索引都失效、每题都全表扫该用户题目（导入 N 题 = N 次全表扫）。
        """
        rows = self._db.execute(
            "SELECT type, stem FROM questions WHERE user_id=?", (user_id,)).fetchall()
        return {(r[0], (r[1] or "").strip()) for r in rows}

    def create_many(self, user_id: str, questions: list[dict]) -> list[str]:
        """批量插入，单事务一次提交。返回新建 id 列表（顺序与入参一致）。

        取代逐题 create() 的「每题一次 commit」——WAL 下每次 commit 都 fsync，
        导入 N 题 = N 次 fsync。调用方须自行完成去重与字段校验。
        """
        if not questions:
            return []
        now = _now()
        ids: list[str] = []
        rows = []
        for q in questions:
            qid = uuid4().hex
            ids.append(qid)
            rows.append((qid, user_id, q["type"], q["stem"],
                         json.dumps(q.get("options"), ensure_ascii=False),
                         json.dumps(q.get("answer"), ensure_ascii=False),
                         q.get("explanation", ""), q.get("source", ""), now))
        self._db.executemany(
            "INSERT INTO questions(id, user_id, type, stem, options, answer, explanation, "
            "source, created_at) VALUES (?,?,?,?,?,?,?,?,?)", rows)
        self._db.commit()
        return ids

    def _row(self, r) -> dict:
        return {"id": r[0], "type": r[1], "stem": r[2],
                "options": json.loads(r[3]), "answer": json.loads(r[4]),
                "explanation": r[5], "source": r[6], "created_at": r[7]}

    def get(self, user_id: str, qid: str) -> dict | None:
        r = self._db.execute(
            f"SELECT {self._COLS} FROM questions WHERE id=? AND user_id=?",
            (qid, user_id)).fetchone()
        return self._row(r) if r else None

    def get_many(self, user_id: str, ids: list[str]) -> list[dict]:
        """按传入 ids 的先后顺序返回题目（SQL 的 IN 不保序，故在此重排）。
        不存在或不属于该用户的 id 直接跳过；重复 id 只返回一次。"""
        if not ids:
            return []
        ph = ",".join("?" * len(ids))
        rows = self._db.execute(
            f"SELECT {self._COLS} FROM questions WHERE user_id=? AND id IN ({ph})",
            (user_id, *ids)).fetchall()
        by_id = {r[0]: self._row(r) for r in rows}
        out, seen = [], set()
        for i in ids:
            if i in by_id and i not in seen:
                seen.add(i)
                out.append(by_id[i])
        return out

    def _filter(self, user_id: str, type, source, q):
        clauses = ["user_id=?"]
        params: list = [user_id]
        if type:
            clauses.append("type=?"); params.append(type)
        if source:
            clauses.append("source=?"); params.append(source)
        if q:
            clauses.append("stem LIKE ?"); params.append(f"%{q}%")
        return " AND ".join(clauses), params

    def list(self, user_id: str, *, type: str | None = None, source: str | None = None,
             q: str | None = None, limit: int | None = None, offset: int = 0) -> list[dict]:
        where, params = self._filter(user_id, type, source, q)
        sql = f"SELECT {self._COLS} FROM questions WHERE {where} ORDER BY created_at DESC"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"; params += [limit, offset]
        return [self._row(r) for r in self._db.execute(sql, params).fetchall()]

    def count(self, user_id: str, *, type: str | None = None, source: str | None = None,
              q: str | None = None) -> int:
        where, params = self._filter(user_id, type, source, q)
        return self._db.execute(
            f"SELECT COUNT(*) FROM questions WHERE {where}", params).fetchone()[0]

    def sources(self, user_id: str) -> list[str]:
        rows = self._db.execute(
            "SELECT DISTINCT source FROM questions WHERE user_id=? AND source<>'' "
            "ORDER BY source", (user_id,)).fetchall()
        return [r[0] for r in rows]

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

# app/exam_session.py
"""服务端托管的模拟考试状态：一会话一场。

判分与「答错必存」由 /api/chat 判分中间件确定性执行，本模块只管状态：
出题队列 + 当前题游标 + 逐题结果 + 模式。用户隔离，随会话持久化（刷新可续考）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .db import migrate, open_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExamSessionStore:
    def __init__(self, db_path: str | None = None, *,
                 conn: sqlite3.Connection | None = None) -> None:
        if conn is not None:
            self._db = conn
        else:
            self._db = open_db(db_path)
            migrate(self._db)

    def start(self, user_id: str, conv_id: str, questions: list[dict], mode: str) -> None:
        """开考：覆盖同会话旧考试。questions 为含答案的题目快照数组。"""
        self._db.execute(
            "INSERT INTO exam_sessions(conversation_id, user_id, mode, questions, cursor, "
            "results, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(conversation_id) DO UPDATE SET user_id=excluded.user_id, "
            "mode=excluded.mode, questions=excluded.questions, cursor=0, results='[]', "
            "status='active', created_at=excluded.created_at, updated_at=excluded.updated_at",
            (conv_id, user_id, mode, json.dumps(questions, ensure_ascii=False), 0,
             "[]", "active", _now(), _now()))
        self._db.commit()

    def get_active(self, user_id: str, conv_id: str) -> dict | None:
        r = self._db.execute(
            "SELECT conversation_id, user_id, mode, questions, cursor, results, status "
            "FROM exam_sessions WHERE conversation_id=? AND user_id=? AND status='active'",
            (conv_id, user_id)).fetchone()
        if r is None:
            return None
        return {"conversation_id": r[0], "user_id": r[1], "mode": r[2],
                "questions": json.loads(r[3]), "cursor": r[4],
                "results": json.loads(r[5]), "status": r[6]}

    @staticmethod
    def current(session: dict) -> dict | None:
        """当前待作答题；游标越界（已答完）返回 None。"""
        qs, i = session["questions"], session["cursor"]
        return qs[i] if 0 <= i < len(qs) else None

    @staticmethod
    def is_finished(session: dict) -> bool:
        return session["cursor"] >= len(session["questions"])

    def record(self, user_id: str, conv_id: str, user_answer, is_correct: bool) -> None:
        """记录本题作答并推进游标。仅对 active 会话生效。"""
        s = self.get_active(user_id, conv_id)
        if s is None:
            return
        results = s["results"]
        results.append({"user_answer": user_answer, "is_correct": bool(is_correct)})
        self._db.execute(
            "UPDATE exam_sessions SET results=?, cursor=cursor+1, updated_at=? "
            "WHERE conversation_id=? AND user_id=?",
            (json.dumps(results, ensure_ascii=False), _now(), conv_id, user_id))
        self._db.commit()

    def end(self, user_id: str, conv_id: str) -> None:
        self._db.execute(
            "UPDATE exam_sessions SET status='ended', updated_at=? "
            "WHERE conversation_id=? AND user_id=?", (_now(), conv_id, user_id))
        self._db.commit()

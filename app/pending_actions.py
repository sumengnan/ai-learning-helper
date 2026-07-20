# app/pending_actions.py
"""待确认的破坏性操作。

agent 不直接执行删除类动作，而是在此登记一条待确认记录并把它交付给用户；用户在前端点
「确认」后由 API 执行真正的动作——**执行者是 API，不是 agent**。这一点决定了本机制的
成本：无需给计划做快照、也无需断点续跑，本轮照常正常收尾即可。

为什么必须有这层：执行子步没有与用户对话的通道（工具表里不存在任何提问工具），
所以 delete_questions 描述里那句「调用前必须先向用户取得确认」在主执行路径上
**结构性地无法满足**——模型要么跳过确认直接删，要么自称确认过了。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .db import migrate, open_db

PENDING = "pending"
CONFIRMED = "confirmed"
REJECTED = "rejected"
EXPIRED = "expired"

# 默认有效期。给足用户看完题目再决定，又不至于让过期卡片长期挂在历史消息里被误点。
DEFAULT_TTL_SECONDS = 24 * 3600


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PendingActionStore:
    def __init__(self, db_path: str | None = None, *,
                 conn: sqlite3.Connection | None = None) -> None:
        if conn is not None:
            self._db = conn
        else:
            self._db = open_db(db_path)
            migrate(self._db)

    _COLS = "id, user_id, conv_id, kind, payload, status, created_at, expires_at, decided_at"

    def _row(self, r) -> dict:
        return {"id": r[0], "user_id": r[1], "conv_id": r[2], "kind": r[3],
                "payload": json.loads(r[4]), "status": r[5], "created_at": r[6],
                "expires_at": r[7], "decided_at": r[8]}

    def create(self, user_id: str, conv_id: str | None, kind: str, payload: dict,
               *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
        pid = uuid4().hex
        now = _now()
        self._db.execute(
            "INSERT INTO pending_actions(id, user_id, conv_id, kind, payload, status, "
            "created_at, expires_at, decided_at) VALUES (?,?,?,?,?,?,?,?,NULL)",
            (pid, user_id, conv_id, kind, json.dumps(payload, ensure_ascii=False),
             PENDING, now.isoformat(),
             (now + timedelta(seconds=ttl_seconds)).isoformat()))
        self._db.commit()
        return pid

    def get(self, user_id: str, pid: str) -> dict | None:
        """按 id 取。归属不符一律当作不存在——不泄露「这个 id 存在但不是你的」。"""
        r = self._db.execute(
            f"SELECT {self._COLS} FROM pending_actions WHERE id=? AND user_id=?",
            (pid, user_id)).fetchone()
        if r is None:
            return None
        d = self._row(r)
        # 过期的对外一律呈现为 expired，但不改库：状态迁移只发生在 decide 里，
        # 读路径保持无副作用（否则并发读会互相竞争写）。
        if d["status"] == PENDING and d["expires_at"] < _now().isoformat():
            d["status"] = EXPIRED
        return d

    def list_pending(self, user_id: str, conv_id: str | None = None) -> list[dict]:
        sql = (f"SELECT {self._COLS} FROM pending_actions "
               "WHERE user_id=? AND status=? AND expires_at>?")
        params: list = [user_id, PENDING, _now().isoformat()]
        if conv_id:
            sql += " AND conv_id=?"; params.append(conv_id)
        sql += " ORDER BY created_at DESC"
        return [self._row(r) for r in self._db.execute(sql, params).fetchall()]

    def decide(self, user_id: str, pid: str, status: str) -> dict | None:
        """把 pending 原子地迁移到 confirmed/rejected。

        返回迁移成功后的记录；若记录不存在/非本人/已决策过/已过期，返回 None。
        原子性靠 `WHERE status='pending' AND expires_at>?` + rowcount 判定：用户连点两次
        「确认」时，只有第一次的 rowcount 为 1，第二次拿到 None——**这是删除不会被执行
        两次的唯一保证**，调用方必须据返回值决定要不要执行动作。
        """
        if status not in (CONFIRMED, REJECTED):
            raise ValueError(f"非法状态：{status}")
        now = _now().isoformat()
        cur = self._db.execute(
            "UPDATE pending_actions SET status=?, decided_at=? "
            "WHERE id=? AND user_id=? AND status=? AND expires_at>?",
            (status, now, pid, user_id, PENDING, now))
        self._db.commit()
        if not cur.rowcount:
            return None
        return self.get(user_id, pid)

    def purge_expired(self) -> int:
        """清掉已过期且仍是 pending 的记录（历史消息里的死卡片）。返回清理条数。"""
        cur = self._db.execute(
            "DELETE FROM pending_actions WHERE status=? AND expires_at<=?",
            (PENDING, _now().isoformat()))
        self._db.commit()
        return cur.rowcount or 0

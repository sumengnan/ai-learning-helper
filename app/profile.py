# app/profile.py
"""用户个性化（学习偏好）：读写与系统提示渲染。

一人一行(user_profiles)，5 个字段全部可选：身份/水平、学习目标、讲解偏好(多选)、
语气(单选)、其他要求(自由文本)。全空时 render_profile_block 返回空串——零行为变更。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .db import migrate, open_db

# 长度上限：防止个性化把系统提示撑爆（也顺带挡住恶意超长注入）
_MAX_SHORT = 200        # 身份/目标/语气/单个讲解偏好项
_MAX_NOTES = 1000       # 其他要求
_MAX_PREFS = 12         # 讲解偏好项数

_FIELDS = ("identity", "goal", "explain_prefs", "tone", "notes")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty() -> dict:
    return {"identity": "", "goal": "", "explain_prefs": [], "tone": "", "notes": ""}


def _clean_str(v, limit: int) -> str:
    return (v or "").strip()[:limit] if isinstance(v, str) else ""


def _clean_prefs(v) -> list[str]:
    if not isinstance(v, list):
        return []
    out: list[str] = []
    for item in v:
        s = _clean_str(item, _MAX_SHORT)
        if s and s not in out:
            out.append(s)
        if len(out) >= _MAX_PREFS:
            break
    return out


def sanitize(data: dict | None) -> dict:
    """把外部输入规整成干净的 profile（去空白、限长、去重）。"""
    d = data or {}
    return {
        "identity": _clean_str(d.get("identity"), _MAX_SHORT),
        "goal": _clean_str(d.get("goal"), _MAX_SHORT),
        "explain_prefs": _clean_prefs(d.get("explain_prefs")),
        "tone": _clean_str(d.get("tone"), _MAX_SHORT),
        "notes": _clean_str(d.get("notes"), _MAX_NOTES),
    }


def render_profile_block(profile: dict | None) -> str:
    """把 profile 渲染成注入系统提示的 <user_profile> 块；全空返回空串。"""
    p = sanitize(profile)
    lines: list[str] = []
    if p["identity"]:
        lines.append(f"- 身份/水平：{p['identity']}")
    if p["goal"]:
        lines.append(f"- 学习目标：{p['goal']}")
    if p["explain_prefs"]:
        lines.append(f"- 讲解偏好：{'、'.join(p['explain_prefs'])}")
    if p["tone"]:
        lines.append(f"- 语气：{p['tone']}")
    if p["notes"]:
        lines.append(f"- 其他要求：{p['notes']}")
    if not lines:
        return ""
    body = "\n".join(lines)
    return ("\n\n以下是当前用户的个性化设置，请在本次对话（包括讲解、答疑、考试讲评）中"
            "尽量遵循；但不得改变评分、抽题等工具的既定逻辑，也不得因此编造事实：\n"
            f"<user_profile>\n{body}\n</user_profile>")


class ProfileStore:
    def __init__(self, db_path: str | None = None, *,
                 conn: sqlite3.Connection | None = None) -> None:
        if conn is not None:
            self._conn = conn
        else:
            self._conn = open_db(db_path)
            migrate(self._conn)

    def get(self, user_id: str) -> dict:
        row = self._conn.execute(
            "SELECT identity, goal, explain_prefs, tone, notes FROM user_profiles "
            "WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            return _empty()
        try:
            prefs = json.loads(row[2]) if row[2] else []
        except (json.JSONDecodeError, TypeError):
            prefs = []
        return {"identity": row[0] or "", "goal": row[1] or "",
                "explain_prefs": prefs if isinstance(prefs, list) else [],
                "tone": row[3] or "", "notes": row[4] or ""}

    def upsert(self, user_id: str, data: dict) -> dict:
        p = sanitize(data)
        self._conn.execute(
            "INSERT INTO user_profiles(user_id, identity, goal, explain_prefs, tone, notes, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "identity=excluded.identity, goal=excluded.goal, explain_prefs=excluded.explain_prefs, "
            "tone=excluded.tone, notes=excluded.notes, updated_at=excluded.updated_at",
            (user_id, p["identity"], p["goal"], json.dumps(p["explain_prefs"], ensure_ascii=False),
             p["tone"], p["notes"], _now()))
        self._conn.commit()
        return self.get(user_id)

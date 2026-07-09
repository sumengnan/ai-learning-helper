# app/_migrations.py
from __future__ import annotations

import sqlite3


def ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    """为已存在的旧表补齐缺失的列（幂等）。

    历史 DB 可能建于某列引入之前，而 CREATE TABLE IF NOT EXISTS 不会改动既有表结构，
    导致读写新列时报 "no such column" / "has no column" 而 500。此处用
    ALTER TABLE ADD COLUMN 补齐缺失列；列已存在时不做任何事。
    """
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.commit()

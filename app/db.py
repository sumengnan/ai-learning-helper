# app/db.py
from __future__ import annotations

import sqlite3

# 应用领域所有表的建表 DDL 集中于此；新增表/字段只改这个模块。
_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS users(
         id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
         password_hash TEXT NOT NULL, salt TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS conversations(
         id TEXT PRIMARY KEY, user_id TEXT, title TEXT, created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS conversation_messages(
         conv_id TEXT, seq INTEGER, role TEXT, content TEXT, tool_calls TEXT,
         tool_call_id TEXT, steps TEXT, progress TEXT, created_at TEXT,
         PRIMARY KEY(conv_id, seq))""",
    """CREATE TABLE IF NOT EXISTS documents(
         id TEXT PRIMARY KEY, user_id TEXT, filename TEXT, size INTEGER, num_chunks INTEGER,
         chunk_ids TEXT, uploaded_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS questions(
         id TEXT PRIMARY KEY, user_id TEXT, type TEXT, stem TEXT, options TEXT,
         answer TEXT, explanation TEXT, source TEXT, created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS wrong_answers(
         id TEXT PRIMARY KEY, user_id TEXT, question_id TEXT, exam_id TEXT,
         snapshot TEXT, user_answer TEXT, created_at TEXT, seq INTEGER)""",
    """CREATE TABLE IF NOT EXISTS downloads(
         id TEXT PRIMARY KEY, user_id TEXT, filename TEXT, size INTEGER,
         content_type TEXT, created_at TEXT, seq INTEGER)""",
    # 聊天附件：裸字节落盘、元数据入库；conv_id 归属会话，发送后经消息 attachments 列关联
    """CREATE TABLE IF NOT EXISTS attachments(
         id TEXT PRIMARY KEY, user_id TEXT, conv_id TEXT, filename TEXT,
         size INTEGER, content_type TEXT, created_at TEXT)""",
    # 会话↔Agent 运行映射：删除会话时据此清理 persistence 库里的检查点/轨迹
    """CREATE TABLE IF NOT EXISTS conversation_runs(
         conv_id TEXT, run_id TEXT, created_at TEXT,
         PRIMARY KEY(conv_id, run_id))""",
)

# 历史库若建于某列引入之前，需在此补齐（CREATE TABLE IF NOT EXISTS 不改既有表结构）
_COLUMN_MIGRATIONS: dict[str, dict[str, str]] = {
    "conversations": {"user_id": "TEXT"},
    "conversation_messages": {"steps": "TEXT", "progress": "TEXT", "attachments": "TEXT"},
    "documents": {"user_id": "TEXT"},
    "questions": {"user_id": "TEXT"},
    "wrong_answers": {"user_id": "TEXT"},
    "downloads": {"user_id": "TEXT"},
}

_SCHEMA_VERSION = 1


def open_db(path: str) -> sqlite3.Connection:
    """打开（或创建）应用数据库连接。WAL 允许同一文件多连接并发读写。"""
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    """为已存在的旧表补齐缺失的列（幂等）。"""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.commit()


def migrate(conn: sqlite3.Connection) -> None:
    """统一迁移入口：集中建表 + 旧库补列，幂等，可安全重复调用。"""
    for ddl in _SCHEMA:
        conn.execute(ddl)
    conn.commit()
    for table, cols in _COLUMN_MIGRATIONS.items():
        ensure_columns(conn, table, cols)
    conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
    conn.commit()

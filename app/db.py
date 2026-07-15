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
    # verify：交付门结构化判定轨迹（JSON）。progress 列存的是渲染用中文文案，
    # 统计「哪层失败率高/平均重答几次」需要未拍扁的 failed[]/hard_failed[]/attempts，故单列。
    # 每条 history 带 run_id，可据此去 harness 库 trajectory_events 捞出该次被否的草稿原文。
    """CREATE TABLE IF NOT EXISTS conversation_messages(
         conv_id TEXT, seq INTEGER, role TEXT, content TEXT, tool_calls TEXT,
         tool_call_id TEXT, steps TEXT, progress TEXT, sources TEXT, verify TEXT,
         created_at TEXT,
         PRIMARY KEY(conv_id, seq))""",
    """CREATE TABLE IF NOT EXISTS documents(
         id TEXT PRIMARY KEY, user_id TEXT, filename TEXT, size INTEGER, num_chunks INTEGER,
         chunk_ids TEXT, uploaded_at TEXT, excerpt TEXT)""",
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
    # L2 滚动摘要：更早历史压缩成一段摘要。up_to_seq 为水位（已覆盖的历史消息前缀长度），
    # 增量摘要只处理水位之后的 delta，永不重摘全历史。
    """CREATE TABLE IF NOT EXISTS conversation_summaries(
         conv_id TEXT PRIMARY KEY, up_to_seq INTEGER NOT NULL,
         summary TEXT NOT NULL, tokens INTEGER NOT NULL, created_at TEXT NOT NULL)""",
    # 用户个性化（学习偏好）：一人一行，聊天/考试讲评时注入系统提示。全空则不注入。
    """CREATE TABLE IF NOT EXISTS user_profiles(
         user_id TEXT PRIMARY KEY, identity TEXT, goal TEXT,
         explain_prefs TEXT, tone TEXT, notes TEXT, updated_at TEXT)""",
    # 抓取失败的网址登记：下次抓前查此表，命中则跳过并让模型换来源。
    # key 是规范化 URL 或域名（scope 区分），until 为到期时间——过期即失效，不永久拉黑。
    # 全局不分用户：网址抓不抓得到是网站的属性，不是用户的属性。
    """CREATE TABLE IF NOT EXISTS url_blocklist(
         key TEXT PRIMARY KEY, scope TEXT NOT NULL, reason TEXT NOT NULL, status INTEGER,
         until TEXT NOT NULL, hits INTEGER NOT NULL DEFAULT 1,
         created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    # 服务端托管的模拟考试：一会话一条 active。questions/results 为 JSON，cursor 指向当前待作答题。
    # 判分与「答错必存」由服务端在 /api/chat 判分中间件里确定性执行，不依赖模型调用工具。
    """CREATE TABLE IF NOT EXISTS exam_sessions(
         conversation_id TEXT PRIMARY KEY, user_id TEXT, mode TEXT,
         questions TEXT, cursor INTEGER, results TEXT, status TEXT,
         created_at TEXT, updated_at TEXT)""",
)

# 历史库若建于某列引入之前，需在此补齐（CREATE TABLE IF NOT EXISTS 不改既有表结构）
_COLUMN_MIGRATIONS: dict[str, dict[str, str]] = {
    "conversations": {"user_id": "TEXT"},
    "conversation_messages": {"steps": "TEXT", "progress": "TEXT", "attachments": "TEXT",
                              "run_id": "TEXT", "status": "TEXT", "sources": "TEXT",
                              "tokens": "INTEGER", "cost": "REAL", "elapsed_ms": "INTEGER",
                              "reasoning": "TEXT", "verify": "TEXT"},
    "documents": {"user_id": "TEXT", "excerpt": "TEXT"},
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

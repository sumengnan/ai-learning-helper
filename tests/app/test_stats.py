import json
import sqlite3
from datetime import datetime, timezone

import pytest

from app.stats import StatsService, _percentile, _step_bucket

FIXED_NOW = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)


def _traj_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE trajectory_events(run_id TEXT, seq INTEGER, type TEXT, "
        "data TEXT, created_at TEXT, PRIMARY KEY(run_id, seq))")
    return conn


def _ev(conn, run_id, seq, typ, data, created_at="2026-07-11T10:00:00+00:00"):
    # 复刻生产存储：data 列存整条事件 {"type","data"}（见 TrajectoryStore.append）
    conn.execute(
        "INSERT INTO trajectory_events(run_id, seq, type, data, created_at) VALUES (?,?,?,?,?)",
        (run_id, seq, typ, json.dumps({"type": typ, "data": data}), created_at))


def _seed_traj(conn):
    # run1：成功，2 步，1 次模型调用(100 tok, 1000ms)，1 次 http_request 成功
    _ev(conn, "r1", 0, "RunStarted", {"run_id": "r1"})
    _ev(conn, "r1", 1, "StepStarted", {"step": 0})
    _ev(conn, "r1", 2, "ModelUsage",
        {"usage": {"prompt": 60, "completion": 40, "total": 100}, "cost_usd": None,
         "attempts": 1, "latency_ms": 1000.0})
    _ev(conn, "r1", 3, "ToolStarted", {"tool_call": {"id": "c1", "name": "http_request", "arguments": {}}})
    _ev(conn, "r1", 4, "ToolFinished", {"result": {"tool_call_id": "c1", "content": "ok", "is_error": False}})
    _ev(conn, "r1", 5, "StepStarted", {"step": 1})
    _ev(conn, "r1", 6, "RunFinished", {"message": {"role": "assistant", "content": "done"}})
    # run2：成功，1 步，1 次模型调用(50 tok, 3000ms, 有成本, 重试)，1 次 run_shell 失败
    _ev(conn, "r2", 0, "RunStarted", {"run_id": "r2"})
    _ev(conn, "r2", 1, "StepStarted", {"step": 0})
    _ev(conn, "r2", 2, "ModelUsage",
        {"usage": {"prompt": 30, "completion": 20, "total": 50}, "cost_usd": 0.01,
         "attempts": 2, "latency_ms": 3000.0})
    _ev(conn, "r2", 3, "ToolStarted", {"tool_call": {"id": "c2", "name": "run_shell", "arguments": {}}})
    _ev(conn, "r2", 4, "ToolFinished", {"result": {"tool_call_id": "c2", "content": "boom", "is_error": True}})
    _ev(conn, "r2", 5, "RunFinished", {"message": {"role": "assistant", "content": "done"}})
    conn.commit()


def _app_conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE documents(id TEXT, user_id TEXT);"
        "CREATE TABLE questions(id TEXT, user_id TEXT);"
        "CREATE TABLE wrong_answers(id TEXT, user_id TEXT);"
        "CREATE TABLE conversations(id TEXT, user_id TEXT, title TEXT, created_at TEXT);"
        "CREATE TABLE conversation_messages(conv_id TEXT, seq INTEGER, created_at TEXT);"
        "CREATE TABLE downloads(id TEXT, user_id TEXT, filename TEXT, size INTEGER, "
        "content_type TEXT, created_at TEXT, seq INTEGER);")
    conn.executemany("INSERT INTO documents VALUES (?,?)", [("d1", "u"), ("d2", "u")])
    conn.execute("INSERT INTO conversations VALUES ('cv1','u','二叉树','2026-07-10T09:00:00+00:00')")
    conn.executemany("INSERT INTO conversation_messages VALUES (?,?,?)",
                     [("cv1", 0, "2026-07-11T08:00:00+00:00"), ("cv1", 1, "2026-07-11T08:05:00+00:00")])
    conn.execute("INSERT INTO downloads(id,user_id,filename,size,content_type,created_at,seq) "
                 "VALUES ('dl1','u','提纲.md',12,'text/markdown','2026-07-11T07:00:00+00:00',1)")
    conn.commit()
    return conn


def _mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE memory_items(id INTEGER PRIMARY KEY, collection TEXT, "
                 "text TEXT, metadata TEXT, created_at TEXT)")
    conn.executemany(
        "INSERT INTO memory_items(collection, text, created_at) VALUES ('semantic', ?, ?)",
        [("a", "2026-07-11T01:00:00+00:00"), ("b", "2026-07-11T02:00:00+00:00"),
         ("c", "2026-07-11T03:00:00+00:00")])
    conn.commit()
    return conn


def _svc():
    tc = _traj_conn(); _seed_traj(tc)
    return StatsService(trajectory_conn=tc, app_conn=_app_conn(),
                        memory_conn=_mem_conn(), now=lambda: FIXED_NOW)


# ---------- 纯函数 ----------

def test_percentile_basic():
    assert _percentile([], 95) == 0.0
    assert _percentile([5], 95) == 5
    assert _percentile([1000.0, 3000.0], 95) == pytest.approx(2900.0)


def test_step_bucket():
    assert [_step_bucket(n) for n in (1, 2, 4, 5, 6, 7, 20)] == ["1", "2", "4", "5-6", "5-6", "7+", "7+"]


# ---------- ops 聚合 ----------

def test_ops_totals():
    t = _svc().overview("u")["ops"]["totals"]
    assert t["runs"] == 2
    assert t["runs_finished"] == 2
    assert t["success_rate"] == 1.0
    assert t["model_calls"] == 2
    assert t["total_tokens"] == 150
    assert t["retries"] == 1
    assert t["cost_usd"] == 0.01
    assert t["avg_latency_ms"] == 2000
    assert t["p95_latency_ms"] == 2900


def test_ops_tools_with_error_correlation():
    tools = {x["name"]: x for x in _svc().overview("u")["ops"]["tools"]}
    assert tools["http_request"]["count"] == 1 and tools["http_request"]["success_rate"] == 1.0
    assert tools["run_shell"]["count"] == 1 and tools["run_shell"]["errors"] == 1
    assert tools["run_shell"]["success_rate"] == 0.0


def test_ops_steps_histogram():
    hist = {b["bucket"]: b["count"] for b in _svc().overview("u")["ops"]["steps_histogram"]}
    # run1=2 步 → 桶"2"，run2=1 步 → 桶"1"
    assert hist["1"] == 1 and hist["2"] == 1 and hist["7+"] == 0


# ---------- learn 组装 ----------

def test_learn_assets_and_abilities():
    learn = _svc().overview("u")["learn"]
    assert learn["assets"] == {"documents": 2, "memory": 3, "questions": 0, "wrong_answers": 0}
    assert learn["conversations"] == 1 and learn["messages"] == 2
    abilities = {a["label"]: a["count"] for a in learn["abilities"]}
    assert abilities["联网查资料"] == 1 and abilities["运行代码"] == 1
    assert learn["effort"]["runs"] == 2 and learn["effort"]["total_tokens"] == 150


def test_learn_last_conversation_and_downloads():
    learn = _svc().overview("u")["learn"]
    assert learn["last_conversation"]["title"] == "二叉树"
    assert learn["last_conversation"]["message_count"] == 2
    # 产物需带 id/content_type/size 供前端预览+下载
    assert learn["recent_downloads"] == [{
        "id": "dl1", "filename": "提纲.md", "content_type": "text/markdown", "size": 12,
        "created_at": "2026-07-11T07:00:00+00:00"}]


def test_memory_items_listing():
    items = _svc().memory_items(limit=10)
    assert len(items) == 3
    assert {i["text"] for i in items} == {"a", "b", "c"}
    assert all("created_at" in i for i in items)
    # 无记忆库降级为空
    from app.stats import StatsService
    svc = StatsService(trajectory_conn=_traj_conn(), app_conn=_app_conn(), memory_conn=None,
                       now=lambda: FIXED_NOW)
    assert svc.memory_items() == []


def test_activity_series_length_and_shape():
    ov = _svc().overview("u", days=14)
    assert ov["range_days"] == 14
    series = ov["learn"]["activity"]
    assert len(series) == 14
    assert series[-1]["date"] == "2026-07-11"
    # 事件都落在 07-11，当天 runs=2 tokens=150
    today = series[-1]
    assert today["runs"] == 2 and today["tokens"] == 150


def test_run_duration_aggregation():
    tc = _traj_conn()
    _ev(tc, "r1", 0, "RunStarted", {"run_id": "r1"}, created_at="2026-07-11T10:00:00+00:00")
    _ev(tc, "r1", 1, "RunFinished", {"message": {}}, created_at="2026-07-11T10:00:05+00:00")
    _ev(tc, "r2", 0, "RunStarted", {"run_id": "r2"}, created_at="2026-07-11T10:01:00+00:00")
    _ev(tc, "r2", 1, "RunFinished", {"message": {}}, created_at="2026-07-11T10:01:03+00:00")
    tc.commit()
    svc = StatsService(trajectory_conn=tc, app_conn=_app_conn(), memory_conn=None,
                       now=lambda: FIXED_NOW)
    t = svc.overview("u")["ops"]["totals"]
    assert t["avg_run_duration_ms"] == 4000       # (5000 + 3000) / 2
    assert t["total_run_duration_ms"] == 8000


def test_empty_databases_do_not_crash():
    svc = StatsService(trajectory_conn=_traj_conn(), app_conn=_app_conn(), memory_conn=None,
                       now=lambda: FIXED_NOW)
    ov = svc.overview("nobody")
    assert ov["ops"]["totals"]["runs"] == 0
    assert ov["learn"]["assets"]["memory"] == 0
    assert ov["learn"]["last_conversation"] is None
    assert ov["ops"]["tools"] == []

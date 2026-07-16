"""分层上下文统计（读 conversation_messages.context 列）。

存在的理由：L2 摘要失败此前是完全静默的——没日志、没指标。而它的后果不轻：被挤出 L1 的
历史已经不在上下文里，摘要再没有，模型就凭空失忆一段，还会照着残缺上下文自信作答。
amnesia_turns 就是把「这种轮次发生了几次」变成一个能盯的数。
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from app.db import migrate
from app.stats import StatsService


def _app_db():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    migrate(conn)
    return conn


def _msg(conn, ctx: dict | None, *, seq: int, days_ago: float = 0):
    at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    conn.execute(
        "INSERT INTO conversation_messages(conv_id, seq, role, content, context, created_at)"
        " VALUES('c1', ?, 'assistant', 'a', ?, ?)",
        (seq, json.dumps(ctx) if ctx is not None else None, at))
    conn.commit()


def _empty_traj():
    """本统计只读应用库的 context 列，轨迹库给个空表即可（overview 仍会去查它）。"""
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE trajectory_events(run_id TEXT, seq INTEGER, type TEXT,"
              " data TEXT, created_at TEXT, PRIMARY KEY(run_id, seq))")
    return c


def _stats(conn) -> dict:
    svc = StatsService(trajectory_conn=_empty_traj(), app_conn=conn, memory_conn=None)
    return svc.overview(days=7, user_id=None)["ops"]["context"]


def test_amnesia_counted_only_when_history_was_actually_lost():
    """摘要失败但没挤出任何历史 → 无害，不该报警；挤出了才是真丢。

    这是本统计的全部要点：把 summary_errors 直接当告警会天天误报。
    """
    conn = _app_db()
    _msg(conn, {"strategy": "layered", "evicted": 8, "summary": "error",
                "summary_error": "RuntimeError: boom"}, seq=1)     # 真丢了 8 条
    _msg(conn, {"strategy": "layered", "evicted": 0, "summary": "error",
                "summary_error": "RuntimeError: boom"}, seq=2)     # 没挤出 → 无害
    s = _stats(conn)
    assert s["summary_errors"] == 2      # 两次都是失败
    assert s["amnesia_turns"] == 1       # 但只有一次真造成了失忆


def test_ok_turns_not_counted_as_amnesia():
    conn = _app_db()
    _msg(conn, {"strategy": "layered", "evicted": 10, "summary": "ok"}, seq=1)
    s = _stats(conn)
    assert s["amnesia_turns"] == 0
    assert s["summary_ok"] == 1
    assert s["evicted_total"] == 10


def test_non_layered_turns_excluded_from_layer_metrics():
    """full 策略没有 L1/L2 之分，不能把它算进分层指标的分母。"""
    conn = _app_db()
    _msg(conn, {"strategy": "full"}, seq=1)
    _msg(conn, {"strategy": "layered", "evicted": 3, "summary": "ok"}, seq=2)
    s = _stats(conn)
    assert s["turns"] == 2 and s["layered_turns"] == 1


def test_retrieval_errors_tracked_separately():
    """L3 挂了只是少了增益，与 L2 失忆不是一回事，不能混在一个数里。"""
    conn = _app_db()
    _msg(conn, {"strategy": "layered", "evicted": 5, "summary": "ok",
                "retrieval": "error", "retrieval_error": "Timeout"}, seq=1)
    s = _stats(conn)
    assert s["retrieval_errors"] == 1
    assert s["amnesia_turns"] == 0       # 摘要成了就没失忆


def test_dirty_json_does_not_500_the_page():
    conn = _app_db()
    conn.execute(
        "INSERT INTO conversation_messages(conv_id, seq, role, content, context, created_at)"
        " VALUES('c1', 1, 'assistant', 'a', '{坏JSON', ?)",
        (datetime.now(timezone.utc).isoformat(),))
    _msg(conn, {"strategy": "layered", "evicted": 2, "summary": "error"}, seq=2)
    conn.commit()
    s = _stats(conn)
    assert s["amnesia_turns"] == 1       # 脏数据被跳过，好数据照常统计


def test_out_of_range_turns_excluded():
    conn = _app_db()
    _msg(conn, {"strategy": "layered", "evicted": 9, "summary": "error"}, seq=1, days_ago=30)
    assert _stats(conn)["amnesia_turns"] == 0

def test_empty_when_no_context_rows():
    assert _stats(_app_db())["turns"] == 0

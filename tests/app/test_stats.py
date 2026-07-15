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
        "CREATE TABLE conversation_messages(conv_id TEXT, seq INTEGER, created_at TEXT, verify TEXT);"
        "CREATE TABLE downloads(id TEXT, user_id TEXT, filename TEXT, size INTEGER, "
        "content_type TEXT, created_at TEXT, seq INTEGER);")
    conn.executemany("INSERT INTO documents VALUES (?,?)", [("d1", "u"), ("d2", "u")])
    conn.execute("INSERT INTO conversations VALUES ('cv1','u','二叉树','2026-07-10T09:00:00+00:00')")
    conn.executemany("INSERT INTO conversation_messages(conv_id, seq, created_at) VALUES (?,?,?)",
                     [("cv1", 0, "2026-07-11T08:00:00+00:00"), ("cv1", 1, "2026-07-11T08:05:00+00:00")])
    conn.execute("INSERT INTO downloads(id,user_id,filename,size,content_type,created_at,seq) "
                 "VALUES ('dl1','u','提纲.md',12,'text/markdown','2026-07-11T07:00:00+00:00',1)")
    conn.commit()
    return conn


def _mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE memory_records(rowid INTEGER PRIMARY KEY, id TEXT, "
                 "owner_id TEXT, kind TEXT, mem_type TEXT, text TEXT, "
                 "superseded INTEGER DEFAULT 0, created_at TEXT)")
    # cv1 属于用户 'u'（见 _app_conn）；这三条是 u 的对话记忆
    conn.executemany(
        "INSERT INTO memory_records(id, owner_id, kind, mem_type, text, created_at) "
        "VALUES (?, 'cv1', 'conversation', 'semantic', ?, ?)",
        [("m1", "a", "2026-07-11T01:00:00+00:00"), ("m2", "b", "2026-07-11T02:00:00+00:00"),
         ("m3", "c", "2026-07-11T03:00:00+00:00")])
    # 干扰项：别的会话(别的用户)的记忆、已取代记忆、知识块——都不应计入 u 的偏好
    conn.execute("INSERT INTO memory_records(id, owner_id, kind, mem_type, text, created_at) "
                 "VALUES ('m4', 'cvX', 'conversation', 'semantic', '别人的', '2026-07-11T04:00:00+00:00')")
    conn.execute("INSERT INTO memory_records(id, owner_id, kind, mem_type, text, superseded, created_at) "
                 "VALUES ('m5', 'cv1', 'conversation', 'semantic', '旧的', 1, '2026-07-11T05:00:00+00:00')")
    conn.execute("INSERT INTO memory_records(id, owner_id, kind, mem_type, text, created_at) "
                 "VALUES ('m6', 'u', 'knowledge', 'semantic', '文档块', '2026-07-11T06:00:00+00:00')")
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


def test_ops_cost_from_tiers_and_currency():
    # 配了分层单价表：成本由 token 现算（回溯历史事件），不依赖事件里存的 cost_usd。
    # run1 输入60/输出40 + run2 输入30/输出20，均落第 1 档（≤256K）：输入1.6、输出6.4 /百万。
    tc = _traj_conn(); _seed_traj(tc)
    svc = StatsService(trajectory_conn=tc, app_conn=_app_conn(), memory_conn=_mem_conn(),
                       price_tiers=[[256000, 1.6, 6.4], [1000000, 4.8, 19.2]],
                       currency="¥", now=lambda: FIXED_NOW)
    t = svc.overview("u")["ops"]["totals"]
    expected = (60 + 30) / 1_000_000 * 1.6 + (40 + 20) / 1_000_000 * 6.4
    assert t["cost_usd"] == round(expected, 4)
    assert t["cost_currency"] == "¥"


def test_ops_cost_currency_default_dollar_without_tiers():
    # 不配分层表：回退累加事件里已存的 cost_usd（0.01），货币符号默认 $
    t = _svc().overview("u")["ops"]["totals"]
    assert t["cost_usd"] == 0.01
    assert t["cost_currency"] == "$"


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
    items = _svc().memory_items("u", limit=10)
    # 只返回本用户会话(cv1)的对话记忆：排除别人的(cvX)、已取代(superseded)、知识块
    assert len(items) == 3
    assert {i["text"] for i in items} == {"a", "b", "c"}
    assert all("created_at" in i for i in items)


def test_memory_items_isolated_by_user():
    # 别的用户看不到 u 的记忆；无会话则返回空
    assert _svc().memory_items("someone-else") == []


def test_memory_items_include_id():
    items = _svc().memory_items("u", limit=10)
    assert all(i.get("id") for i in items)          # 删除要用到 id


def test_memory_items_no_mem_db():
    from app.stats import StatsService
    svc = StatsService(trajectory_conn=_traj_conn(), app_conn=_app_conn(), memory_conn=None,
                       now=lambda: FIXED_NOW)
    assert svc.memory_items("u") == []


# ---------- delete_memory ----------

class _FakeMemStore:
    """最小写后端替身：记录 delete 调用，按 id → owner_id 返回记录。"""
    def __init__(self, owner_by_id: dict):
        self._o = dict(owner_by_id)
        self.deleted: list[str] = []

    def get(self, ids):
        from types import SimpleNamespace
        return [SimpleNamespace(owner_id=self._o[i]) for i in ids if i in self._o]

    def delete(self, ids):
        self.deleted.extend(ids)
        for i in ids:
            self._o.pop(i, None)


def _svc_with_store(store):
    return StatsService(trajectory_conn=_traj_conn(), app_conn=_app_conn(),
                        memory_conn=_mem_conn(), memory_store=store, now=lambda: FIXED_NOW)


def test_delete_memory_owned():
    fake = _FakeMemStore({"m1": "cv1"})            # cv1 属于用户 u（见 _app_conn）
    assert _svc_with_store(fake).delete_memory("u", "m1") is True
    assert fake.deleted == ["m1"]


def test_delete_memory_rejects_other_users_record():
    fake = _FakeMemStore({"mx": "cvX"})            # cvX 不属于 u
    assert _svc_with_store(fake).delete_memory("u", "mx") is False
    assert fake.deleted == []                       # 未删


def test_delete_memory_missing_returns_false():
    fake = _FakeMemStore({})
    assert _svc_with_store(fake).delete_memory("u", "nope") is False


def test_delete_memory_no_store_returns_false():
    svc = StatsService(trajectory_conn=_traj_conn(), app_conn=_app_conn(),
                       memory_conn=_mem_conn(), memory_store=None, now=lambda: FIXED_NOW)
    assert svc.delete_memory("u", "m1") is False


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


# ---------- 交付门重答统计（读 conversation_messages.verify 列）----------

def _vt(attempts, retries, ok, degraded, history):
    return json.dumps({"attempts": attempts, "retries": retries, "ok": ok,
                       "degraded": degraded, "history": history}, ensure_ascii=False)


def _app_conn_with_gate(rows):
    """rows: [(created_at, verify_json)]，都挂在用户 u 的会话 cv1 下。"""
    conn = _app_conn()
    conn.executemany(
        "INSERT INTO conversation_messages(conv_id, seq, created_at, verify) "
        "VALUES ('cv1', ?, ?, ?)",
        [(10 + i, ts, v) for i, (ts, v) in enumerate(rows)])
    conn.commit()
    return conn


# ---------- 回答质量聚合（读 progress 列 scope=quality 项）----------

def _quality_app_conn():
    """带 progress 列的 app 库（_app_conn 那份刻意不带，用于验证缺列时的优雅降级）。"""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE documents(id TEXT, user_id TEXT);"
        "CREATE TABLE questions(id TEXT, user_id TEXT);"
        "CREATE TABLE wrong_answers(id TEXT, user_id TEXT);"
        "CREATE TABLE conversations(id TEXT, user_id TEXT, title TEXT, created_at TEXT);"
        "CREATE TABLE conversation_messages(conv_id TEXT, seq INTEGER, created_at TEXT, "
        "progress TEXT);"
        "CREATE TABLE downloads(id TEXT, user_id TEXT, filename TEXT, size INTEGER, "
        "content_type TEXT, created_at TEXT, seq INTEGER);")
    conn.execute("INSERT INTO conversations VALUES ('cv1','u','会话','2026-07-10T09:00:00+00:00')")
    conn.execute("INSERT INTO conversations VALUES ('cv9','other','别人的','2026-07-10T09:00:00+00:00')")
    conn.commit()
    return conn


def _turn(conn, items, conv="cv1", seq=0, created_at="2026-07-11T08:00:00+00:00"):
    """写一轮助手消息的 progress（形状同 chat.py 落库的那份）。"""
    conn.execute("INSERT INTO conversation_messages(conv_id, seq, created_at, progress) "
                 "VALUES (?,?,?,?)",
                 (conv, seq, created_at, json.dumps(items, ensure_ascii=False)))
    conn.commit()


def _quality(final=None, plan=None, steps=None):
    return {"scope": "quality", "key": "quality", "status": "ok",
            "text": json.dumps({"plan": plan, "steps": steps, "final": final, "feedback": ""})}



def _quality_svc(app_conn):
    return StatsService(trajectory_conn=_traj_conn(), app_conn=app_conn,
                        memory_conn=None, now=lambda: FIXED_NOW)


def test_quality_scores_aggregated():
    app = _quality_app_conn()
    _turn(app, [_quality(final=90, plan=80, steps=70)], seq=0)
    _turn(app, [_quality(final=60, plan=60, steps=50)], seq=1)
    q = _quality_svc(app).overview("u")["ops"]["quality"]
    assert q["scored_turns"] == 2
    assert q["avg_final"] == 75.0
    assert q["avg_plan"] == 70.0
    assert q["avg_steps"] == 60.0


def test_quality_distribution_buckets_are_stable():
    app = _quality_app_conn()
    for i, s in enumerate([95, 85, 70, 30]):
        _turn(app, [_quality(final=s)], seq=i)
    dist = _quality_svc(app).overview("u")["ops"]["quality"]["distribution"]
    # 桶集合固定（含空桶），否则分布图会随数据忽长忽短
    assert [d["bucket"] for d in dist] == ["0-59", "60-79", "80-89", "90-100"]
    assert [d["count"] for d in dist] == [1, 1, 1, 1]





def test_quality_is_scoped_to_user():
    app = _quality_app_conn()
    _turn(app, [_quality(final=90)], conv="cv1", seq=0)
    _turn(app, [_quality(final=10)], conv="cv9", seq=0)   # 别人的会话
    q = _quality_svc(app).overview("u")["ops"]["quality"]
    assert q["scored_turns"] == 1 and q["avg_final"] == 90.0


def test_quality_empty_when_judge_never_ran():
    """轨迹 judge 默认关闭，故默认配置下这块本就是空的 —— 不能崩，也不能瞎编。"""
    q = _quality_svc(_quality_app_conn()).overview("u")["ops"]["quality"]
    assert q["scored_turns"] == 0
    assert q["avg_final"] is None and q["avg_plan"] is None and q["avg_steps"] is None
    assert [d["count"] for d in q["distribution"]] == [0, 0, 0, 0]


def test_malformed_progress_rows_are_skipped_not_fatal():
    app = _quality_app_conn()
    app.execute("INSERT INTO conversation_messages(conv_id, seq, created_at, progress) "
                "VALUES ('cv1', 0, '2026-07-11T08:00:00+00:00', '{坏 JSON')")
    app.execute("INSERT INTO conversation_messages(conv_id, seq, created_at, progress) "
                "VALUES ('cv1', 1, '2026-07-11T08:01:00+00:00', '\"不是列表\"')")
    app.commit()
    _turn(app, [_quality(final=88)], seq=2)
    q = _quality_svc(app).overview("u")["ops"]["quality"]
    assert q["scored_turns"] == 1 and q["avg_final"] == 88.0


def test_quality_text_not_json_is_skipped():
    app = _quality_app_conn()
    _turn(app, [{"scope": "quality", "key": "quality", "status": "ok", "text": "不是 JSON"}], seq=0)
    assert _quality_svc(app).overview("u")["ops"]["quality"]["scored_turns"] == 0


def test_missing_progress_column_degrades_gracefully():
    """库里没有 progress 列（旧库）→ 整块降级为空，不抛。"""
    q = _quality_svc(_app_conn()).overview("u")["ops"]["quality"]
    assert q["scored_turns"] == 0 and q["avg_final"] is None


# ---------- 交付门重答统计的用例 ----------

def _gate_svc(rows):
    tc = _traj_conn(); _seed_traj(tc)
    return StatsService(trajectory_conn=tc, app_conn=_app_conn_with_gate(rows),
                        memory_conn=_mem_conn(), now=lambda: FIXED_NOW)


_IN_RANGE = "2026-07-11T08:00:00+00:00"


def test_gate_stats_aggregates_retries_and_layers():
    svc = _gate_svc([
        # 一次过
        (_IN_RANGE, _vt(1, 0, True, False, [{"attempt": 1, "ok": True, "failed": []}])),
        # 重答一次后过：judge + grounding 两层各记一次
        (_IN_RANGE, _vt(2, 1, True, False, [
            {"attempt": 1, "ok": False, "failed": ["judge", "grounding"]},
            {"attempt": 2, "ok": True, "failed": []}])),
        # 用尽次数降级交付
        (_IN_RANGE, _vt(2, 1, False, True, [
            {"attempt": 1, "ok": False, "failed": ["judge"]},
            {"attempt": 2, "ok": False, "failed": ["judge"]}])),
    ])
    g = svc.overview("u")["ops"]["gate"]
    assert g["turns"] == 3 and g["retries"] == 2
    assert g["avg_retries"] == round(2 / 3, 3)
    assert g["degraded"] == 1 and g["degraded_rate"] == round(1 / 3, 3)
    assert g["first_pass_rate"] == round(1 / 3, 3)     # 仅第 1 条一次过
    layers = {x["layer"]: x["count"] for x in g["layer_failures"]}
    assert layers == {"judge": 3, "grounding": 1}      # 哪层最爱拦
    assert g["layer_failures"][0]["layer"] == "judge"  # 按次数降序
    assert g["layer_failures"][0]["zh"]                # 带中文层名，供前端直接显示


def test_gate_stats_is_separate_from_llm_network_retries():
    # ops.totals.retries 统计的是 LLM 网络重试，与交付门重答是两个口径，别混
    svc = _gate_svc([(_IN_RANGE, _vt(3, 2, True, False, [
        {"attempt": 1, "ok": False, "failed": ["facts"]},
        {"attempt": 2, "ok": False, "failed": ["facts"]},
        {"attempt": 3, "ok": True, "failed": []}]))])
    ops = svc.overview("u")["ops"]
    # 两个数字同时不同值，正是「口径不同」的证明：
    assert ops["gate"]["retries"] == 2          # 交付门重答 2 次（来自 verify 列）
    assert ops["totals"]["retries"] == 1        # LLM 网络重试 1 次（来自轨迹 ModelUsage.attempts）


def test_gate_stats_empty_when_no_verify_rows():
    svc = _gate_svc([])
    g = svc.overview("u")["ops"]["gate"]
    assert g["turns"] == 0 and g["avg_retries"] == 0.0 and g["layer_failures"] == []


def test_gate_stats_excludes_other_users_and_out_of_range():
    conn = _app_conn_with_gate([(_IN_RANGE, _vt(2, 1, True, False, []))])
    # 别的用户的会话
    conn.execute("INSERT INTO conversations VALUES ('cvX','other','x','2026-07-10T09:00:00+00:00')")
    conn.execute("INSERT INTO conversation_messages(conv_id, seq, created_at, verify) "
                 "VALUES ('cvX', 0, ?, ?)", (_IN_RANGE, _vt(5, 4, True, False, [])))
    # 本人但超出时间窗（默认 14 天）
    conn.execute("INSERT INTO conversation_messages(conv_id, seq, created_at, verify) "
                 "VALUES ('cv1', 99, '2020-01-01T00:00:00+00:00', ?)",
                 (_vt(9, 8, True, False, []),))
    conn.commit()
    tc = _traj_conn(); _seed_traj(tc)
    svc = StatsService(trajectory_conn=tc, app_conn=conn, memory_conn=_mem_conn(),
                       now=lambda: FIXED_NOW)
    g = svc.overview("u")["ops"]["gate"]
    assert g["turns"] == 1 and g["retries"] == 1      # 只算本人、窗口内


def test_gate_stats_survives_corrupt_json():
    # 脏数据不该让整个统计页 500
    svc = _gate_svc([(_IN_RANGE, "{不是合法 JSON"),
                     (_IN_RANGE, _vt(2, 1, True, False, []))])
    g = svc.overview("u")["ops"]["gate"]
    assert g["turns"] == 1 and g["retries"] == 1      # 跳过坏行，好行照常算

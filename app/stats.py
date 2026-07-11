# app/stats.py
"""首页概览统计聚合层。

单一职责：把 harness 运行轨迹（harness.db / trajectory_events）与应用业务数据
（app.db / memory.db）聚合成首页需要的指标 dict。纯读、无副作用、不碰 HTTP，
可脱离 FastAPI 单测。

产出同时服务两种视角，但用同一份聚合：
- learn（学习主场）：把运行数据翻译成产品语言（AI 用过哪些能力、花了多少力气）+ 学习资产
- ops（工程台）：运维口径（成功率、P95 延迟、工具成功率、步数分布）

运行轨迹是全局的（harness.db 无 user_id，本应用单用户）；学习资产按 user_id 归属。
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

# 原始工具名 → 面向学习者的「能力」分组（图标, 标签, 归入的工具名集合）。
# 未列出的工具归入「其他能力」。顺序即展示顺序的兜底（实际按调用次数倒排）。
_ABILITY_GROUPS: list[tuple[str, str, set[str]]] = [
    ("🌐", "联网查资料", {"http_request", "browse"}),
    ("💻", "运行代码", {"run_shell", "run_python", "run_java", "run_node"}),
    ("🧠", "记住你的偏好", {"remember", "recall_episodes"}),
    ("🔍", "检索长期记忆", {"search_memory"}),
    ("🧩", "拆解复杂任务", {"dispatch"}),
    ("✍️", "生成文件产物", {"write_file", "save_download"}),
    ("✏️", "出练习题", {"sample_questions"}),
    ("📄", "读你的资料", {"read_attachment", "list_attachments", "read_file", "list_files"}),
]


def _percentile(values: list[float], p: float) -> float:
    """线性插值分位数。空列表返回 0。"""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p / 100.0
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] + (s[c] - s[f]) * (k - f)


def _step_bucket(n: int) -> str:
    if n <= 1:
        return "1"
    if n <= 4:
        return str(n)
    if n <= 6:
        return "5-6"
    return "7+"


_STEP_ORDER = ["1", "2", "3", "4", "5-6", "7+"]


class StatsService:
    def __init__(self, *, trajectory_conn: sqlite3.Connection,
                 app_conn: sqlite3.Connection,
                 memory_conn: sqlite3.Connection | None = None,
                 now=None) -> None:
        self._traj = trajectory_conn
        self._app = app_conn
        self._mem = memory_conn
        self._now = now or (lambda: datetime.now(timezone.utc))

    # ---------- 公开入口 ----------

    def overview(self, user_id: str | None, days: int = 14) -> dict:
        days = max(1, min(days, 90))
        now = self._now()
        cutoff = now - timedelta(days=days)
        events = self._load_events(cutoff.isoformat())
        agg = self._aggregate(events)
        series = self._daily_series(agg["daily"], now, days)
        app_counts = self._app_counts(user_id)
        return {
            "range_days": days,
            "learn": self._learn_section(user_id, agg, series, app_counts),
            "ops": self._ops_section(agg, series, app_counts),
        }

    # ---------- 轨迹聚合 ----------

    def _load_events(self, cutoff_iso: str) -> list[tuple[str, str, str, dict]]:
        try:
            rows = self._traj.execute(
                "SELECT run_id, type, created_at, data FROM trajectory_events "
                "WHERE created_at >= ? ORDER BY created_at", (cutoff_iso,)).fetchall()
        except sqlite3.Error:
            return []
        out = []
        for run_id, typ, created_at, data in rows:
            try:
                d = json.loads(data) if data else {}
            except (json.JSONDecodeError, TypeError):
                d = {}
            # data 列存的是整条序列化事件 {"type":..., "data":{...}}（见 TrajectoryStore.append），
            # 解开外层拿到真正的 payload；已是内层 payload 时原样返回（兼容）。
            if isinstance(d, dict) and "type" in d and "data" in d:
                d = d["data"]
            out.append((run_id, typ, created_at, d if isinstance(d, dict) else {}))
        return out

    def _aggregate(self, events) -> dict:
        runs_started: set[str] = set()
        runs_finished: set[str] = set()
        runs_error: set[str] = set()
        latencies: list[float] = []
        total_prompt = total_completion = total_tokens = 0
        model_calls = 0
        retries = 0
        total_cost = 0.0
        any_cost = False
        tool_name_by_id: dict[str, str] = {}
        tool_counts: Counter = Counter()
        tool_errors: Counter = Counter()
        steps_per_run: Counter = Counter()
        daily: dict[str, dict] = defaultdict(lambda: {"runs": 0, "tokens": 0})

        for run_id, typ, created_at, d in events:
            day = (created_at or "")[:10]
            if typ == "RunStarted":
                runs_started.add(run_id)
                if day:
                    daily[day]["runs"] += 1
            elif typ == "RunFinished":
                runs_finished.add(run_id)
            elif typ == "RunError":
                runs_error.add(run_id)
            elif typ == "StepStarted":
                steps_per_run[run_id] += 1
            elif typ == "ModelUsage":
                model_calls += 1
                u = d.get("usage", {}) or {}
                total_prompt += u.get("prompt", 0) or 0
                total_completion += u.get("completion", 0) or 0
                tok = u.get("total", 0) or 0
                total_tokens += tok
                if day:
                    daily[day]["tokens"] += tok
                lat = d.get("latency_ms")
                if isinstance(lat, (int, float)):
                    latencies.append(float(lat))
                if (d.get("attempts") or 1) > 1:
                    retries += 1
                cost = d.get("cost_usd")
                if isinstance(cost, (int, float)):
                    any_cost = True
                    total_cost += float(cost)
            elif typ == "ToolStarted":
                tc = d.get("tool_call", {}) or {}
                name = tc.get("name") or "?"
                tid = tc.get("id")
                tool_counts[name] += 1
                if tid:
                    tool_name_by_id[tid] = name
            elif typ == "ToolFinished":
                res = d.get("result", {}) or {}
                if res.get("is_error"):
                    name = tool_name_by_id.get(res.get("tool_call_id"))
                    if name:
                        tool_errors[name] += 1

        n_started = len(runs_started) or 0
        success_rate = (len(runs_finished) / n_started) if n_started else 0.0
        step_vals = list(steps_per_run.values())
        return {
            "runs_started": n_started,
            "runs_finished": len(runs_finished),
            "runs_error": len(runs_error),
            "success_rate": min(1.0, success_rate),
            "model_calls": model_calls,
            "total_prompt": total_prompt,
            "total_completion": total_completion,
            "total_tokens": total_tokens,
            "retries": retries,
            "cost_usd": round(total_cost, 4) if any_cost else None,
            "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
            "p95_latency_ms": round(_percentile(latencies, 95)),
            "avg_steps": round(sum(step_vals) / len(step_vals), 1) if step_vals else 0.0,
            "max_steps": max(step_vals) if step_vals else 0,
            "total_steps": sum(step_vals),
            "tool_counts": tool_counts,
            "tool_errors": tool_errors,
            "steps_per_run": steps_per_run,
            "daily": daily,
        }

    def _daily_series(self, daily: dict, now: datetime, days: int) -> list[dict]:
        end = now.date()
        out = []
        for i in range(days - 1, -1, -1):
            key = (end - timedelta(days=i)).isoformat()
            e = daily.get(key, {"runs": 0, "tokens": 0})
            out.append({"date": key, "runs": e["runs"], "tokens": e["tokens"]})
        return out

    def _abilities(self, tool_counts: Counter) -> list[dict]:
        out = []
        grouped: set[str] = set()
        for icon, label, names in _ABILITY_GROUPS:
            c = sum(tool_counts.get(n, 0) for n in names)
            grouped |= names
            if c > 0:
                out.append({"icon": icon, "label": label, "count": c})
        other = sum(v for k, v in tool_counts.items() if k not in grouped)
        if other > 0:
            out.append({"icon": "🛠️", "label": "其他能力", "count": other})
        out.sort(key=lambda x: x["count"], reverse=True)
        return out

    def _tools_list(self, tool_counts: Counter, tool_errors: Counter) -> list[dict]:
        out = []
        for name, count in tool_counts.most_common():
            errs = tool_errors.get(name, 0)
            out.append({
                "name": name, "count": count, "errors": errs,
                "success_rate": round(1 - errs / count, 3) if count else 1.0,
            })
        return out

    def _steps_histogram(self, steps_per_run: Counter) -> list[dict]:
        buckets: Counter = Counter()
        for n in steps_per_run.values():
            buckets[_step_bucket(n)] += 1
        return [{"bucket": b, "count": buckets.get(b, 0)} for b in _STEP_ORDER]

    # ---------- 业务数据 ----------

    def _scalar(self, conn: sqlite3.Connection | None, sql: str, params=()) -> int:
        if conn is None:
            return 0
        try:
            r = conn.execute(sql, params).fetchone()
            return int(r[0]) if r and r[0] is not None else 0
        except sqlite3.Error:
            return 0

    def _app_counts(self, user_id: str | None) -> dict:
        return {
            "documents": self._scalar(
                self._app, "SELECT COUNT(*) FROM documents WHERE user_id=?", (user_id,)),
            "questions": self._scalar(
                self._app, "SELECT COUNT(*) FROM questions WHERE user_id=?", (user_id,)),
            "wrong_answers": self._scalar(
                self._app, "SELECT COUNT(*) FROM wrong_answers WHERE user_id=?", (user_id,)),
            "conversations": self._scalar(
                self._app, "SELECT COUNT(*) FROM conversations WHERE user_id=?", (user_id,)),
            "messages": self._scalar(
                self._app,
                "SELECT COUNT(*) FROM conversation_messages WHERE conv_id IN "
                "(SELECT id FROM conversations WHERE user_id=?)", (user_id,)),
            "memory": self._scalar(self._mem, "SELECT COUNT(*) FROM memory_items"),
        }

    def _recent_downloads(self, user_id: str | None) -> list[dict]:
        if self._app is None:
            return []
        try:
            rows = self._app.execute(
                "SELECT id, filename, content_type, size, created_at FROM downloads WHERE user_id=? "
                "ORDER BY seq DESC LIMIT 3", (user_id,)).fetchall()
        except sqlite3.Error:
            return []
        return [{"id": r[0], "filename": r[1], "content_type": r[2], "size": r[3],
                 "created_at": r[4]} for r in rows]

    def memory_items(self, limit: int = 50) -> list[dict]:
        """列出最近的记忆条目（供首页「AI 记住的偏好」查看）。记忆库缺失时返回空。"""
        if self._mem is None:
            return []
        limit = max(1, min(limit, 200))
        try:
            rows = self._mem.execute(
                "SELECT text, collection, created_at FROM memory_items "
                "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        except sqlite3.Error:
            return []
        return [{"text": r[0], "collection": r[1], "created_at": r[2]} for r in rows]

    def _last_conversation(self, user_id: str | None) -> dict | None:
        if self._app is None:
            return None
        try:
            r = self._app.execute(
                "SELECT c.id, c.title, COALESCE(MAX(m.created_at), c.created_at) AS last_at, "
                "COUNT(m.seq) AS cnt "
                "FROM conversations c LEFT JOIN conversation_messages m ON m.conv_id = c.id "
                "WHERE c.user_id=? GROUP BY c.id ORDER BY last_at DESC LIMIT 1", (user_id,)).fetchone()
        except sqlite3.Error:
            return None
        if not r:
            return None
        return {"id": r[0], "title": r[1] or "未命名对话",
                "updated_at": r[2], "message_count": int(r[3] or 0)}

    # ---------- 组装 ----------

    def _learn_section(self, user_id, agg, series, app_counts) -> dict:
        return {
            "assets": {
                "documents": app_counts["documents"],
                "memory": app_counts["memory"],
                "questions": app_counts["questions"],
                "wrong_answers": app_counts["wrong_answers"],
            },
            "conversations": app_counts["conversations"],
            "messages": app_counts["messages"],
            "recent_downloads": self._recent_downloads(user_id),
            "last_conversation": self._last_conversation(user_id),
            "abilities": self._abilities(agg["tool_counts"]),
            "effort": {
                "runs": agg["runs_started"],
                "total_tokens": agg["total_tokens"],
                "avg_steps": agg["avg_steps"],
                "max_steps": agg["max_steps"],
                "success_rate": round(agg["success_rate"], 3),
            },
            "activity": series,
        }

    def _ops_section(self, agg, series, app_counts) -> dict:
        return {
            "totals": {
                "runs": agg["runs_started"],
                "runs_finished": agg["runs_finished"],
                "runs_error": agg["runs_error"],
                "success_rate": round(agg["success_rate"], 3),
                "model_calls": agg["model_calls"],
                "total_tokens": agg["total_tokens"],
                "total_prompt": agg["total_prompt"],
                "total_completion": agg["total_completion"],
                "avg_latency_ms": agg["avg_latency_ms"],
                "p95_latency_ms": agg["p95_latency_ms"],
                "retries": agg["retries"],
                "cost_usd": agg["cost_usd"],
                "conversations": app_counts["conversations"],
                "messages": app_counts["messages"],
            },
            "daily": series,
            "tools": self._tools_list(agg["tool_counts"], agg["tool_errors"]),
            "steps_histogram": self._steps_histogram(agg["steps_per_run"]),
        }

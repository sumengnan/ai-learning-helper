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

from harness.usage import tiered_cost

from .verify import failed_layers_zh

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


def _iso_delta_ms(start: str, end: str) -> float | None:
    """两个 ISO 时间字符串之差（毫秒）；解析失败返回 None。"""
    try:
        a = datetime.fromisoformat(start)
        b = datetime.fromisoformat(end)
    except (ValueError, TypeError):
        return None
    return (b - a).total_seconds() * 1000.0


def _step_bucket(n: int) -> str:
    if n <= 1:
        return "1"
    if n <= 4:
        return str(n)
    if n <= 6:
        return "5-6"
    return "7+"


_STEP_ORDER = ["1", "2", "3", "4", "5-6", "7+"]

# 质量分分桶。桶名即前端展示的标签；空桶也保留，否则分布图会随数据忽长忽短。
_SCORE_ORDER = ["0-59", "60-79", "80-89", "90-100"]


def _score_bucket(n: int) -> str:
    if n < 60:
        return "0-59"
    if n < 80:
        return "60-79"
    if n < 90:
        return "80-89"
    return "90-100"


def _score_buckets(scores: list[int]) -> list[dict]:
    c = Counter(_score_bucket(s) for s in scores)
    return [{"bucket": b, "count": c.get(b, 0)} for b in _SCORE_ORDER]


class StatsService:
    def __init__(self, *, trajectory_conn: sqlite3.Connection,
                 app_conn: sqlite3.Connection,
                 memory_conn: sqlite3.Connection | None = None,
                 memory_store=None,
                 price_tiers: list | None = None,
                 currency: str = "$",
                 now=None) -> None:
        self._traj = trajectory_conn
        self._app = app_conn
        self._mem = memory_conn
        # 记忆的写侧后端（SqliteVecBackend）：删除要同步清 records/vec/fts 三表，
        # 走它才安全；只读统计仍用 _mem 连接。缺省 None 时删除不可用（优雅降级）。
        self._mem_store = memory_store
        # 分层单价表（按输入长度分档）；配置后由 token 数现算成本，可回溯历史事件。
        # 为空则回退累加事件里已存的 cost_usd（旧口径）。
        self._price_tiers = price_tiers or []
        self._currency = currency
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
            # 学习主场：本人的资产与进度
            "learn": self._learn_section(user_id, agg, series, app_counts),
            # AI 运行统计：运维口径，整页全局、不按用户切
            "ops": self._ops_section(agg, series, self._global_counts(),
                                     self._gate_stats(cutoff.isoformat()),
                                     self._quality_section(
                                         self._load_progress_rows(cutoff.isoformat()))),
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

    # ---------- 回答质量聚合 ----------

    def _load_progress_rows(self, cutoff_iso: str) -> list[tuple[str, list]]:
        """取区间内每轮助手消息的 progress 列表。**全局**，不按用户切。

        「AI 运行统计」整页是运维口径（轨迹库 trajectory_events 本就没有 user_id 列，切不了），
        质量分若按用户切会和同页其它指标对不上，故这里也不带 user 条件。

        用 Python 解析而非 SQLite json_each：progress 是 list-of-dict 的 JSON 串，其中
        quality 项的 text **又是**一层 JSON 串，SQL 要写 json_extract 套 json_extract 套
        json_each，可读性崩塌；且 json_each 遇到一行非法 JSON 会抛 OperationalError 把整个
        查询打挂，Python 侧能逐行跳过（照 _load_events 的既有约定）。
        """
        if self._app is None:
            return []
        try:
            rows = self._app.execute(
                "SELECT created_at, progress FROM conversation_messages "
                "WHERE created_at >= ? AND progress IS NOT NULL "
                "ORDER BY created_at", (cutoff_iso,)).fetchall()
        except sqlite3.Error:
            return []
        out: list[tuple[str, list]] = []
        for created_at, raw in rows:
            try:
                items = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(items, list):
                out.append((created_at or "", items))
        return out

    def _quality_agg(self, rows: list[tuple[str, list]]) -> dict:
        """从 progress 抽 scope=quality —— 轨迹 judge 的分层打分。

        _emit_quality 存的 text 是 {"plan","steps","final","feedback"} 的 JSON（外层 progress
        是列表 JSON，故这里是两层）。交付门的重答/拦截统计不在这里，走 _gate_stats（读专门的
        verify 列，比从渲染用文案里反推可靠）。
        """
        finals: list[int] = []
        plans: list[int] = []
        steps_: list[int] = []
        for _created_at, items in rows:
            for it in items:
                if not isinstance(it, dict) or it.get("scope") != "quality":
                    continue
                try:
                    v = json.loads(it.get("text") or "{}")
                except (json.JSONDecodeError, TypeError):
                    continue
                for key, sink in (("final", finals), ("plan", plans), ("steps", steps_)):
                    if isinstance(v.get(key), int):
                        sink.append(v[key])
        return {"finals": finals, "plans": plans, "steps": steps_}

    def _quality_section(self, rows: list[tuple[str, list]]) -> dict:
        agg = self._quality_agg(rows)
        finals = agg["finals"]
        return {
            "scored_turns": len(finals),
            "avg_final": round(sum(finals) / len(finals), 1) if finals else None,
            "avg_plan": (round(sum(agg["plans"]) / len(agg["plans"]), 1)
                         if agg["plans"] else None),
            "avg_steps": (round(sum(agg["steps"]) / len(agg["steps"]), 1)
                          if agg["steps"] else None),
            # {bucket,count} 形状 —— 前端 StepsHistogram 直接吃，无需新图表组件
            "distribution": _score_buckets(finals),
        }

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
        run_start: dict[str, str] = {}
        run_end: dict[str, str] = {}

        for run_id, typ, created_at, d in events:
            day = (created_at or "")[:10]
            if typ == "RunStarted":
                runs_started.add(run_id)
                if created_at:
                    run_start[run_id] = created_at
                if day:
                    daily[day]["runs"] += 1
            elif typ == "RunFinished":
                runs_finished.add(run_id)
                if created_at:
                    run_end[run_id] = created_at
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
                if self._price_tiers:
                    # 由本次调用的输入/输出 token 现算，历史事件也能回溯出成本
                    c = tiered_cost(u.get("prompt", 0) or 0, u.get("completion", 0) or 0,
                                    self._price_tiers)
                    if c is not None:
                        any_cost = True
                        total_cost += c
                else:
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
        # 每个 run 的端到端耗时（RunStarted→RunFinished 墙钟）
        durations: list[float] = []
        for rid, start in run_start.items():
            end = run_end.get(rid)
            if end:
                ms = _iso_delta_ms(start, end)
                if ms is not None and ms >= 0:
                    durations.append(ms)
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
            "avg_run_duration_ms": round(sum(durations) / len(durations)) if durations else 0,
            "total_run_duration_ms": round(sum(durations)),
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

    def _global_counts(self) -> dict:
        """全站会话/消息数，供「AI 运行统计」用 —— 该页是运维口径，全页不按用户切。
        学习主场那边要的是本人的，走 _app_counts。"""
        return {
            "conversations": self._scalar(self._app, "SELECT COUNT(*) FROM conversations"),
            "messages": self._scalar(self._app, "SELECT COUNT(*) FROM conversation_messages"),
        }

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
            "memory": self._count_user_memories(user_id),
        }

    def _gate_stats(self, cutoff_iso: str) -> dict:
        """交付门重答统计，读 conversation_messages.verify 列。**全局**，不按用户切
        （与同页其它运维指标同口径，见 _load_progress_rows）。

        与 ops.totals.retries 是两回事：那个统计的是 LLM 网络重试（超时/限流后重发请求），
        与交付门无关。这里统计的是「答案没过校验、带反馈重答」的次数。
        """
        empty = {"turns": 0, "retries": 0, "avg_retries": 0.0, "degraded": 0,
                 "degraded_rate": 0.0, "first_pass_rate": 0.0, "gate_errors": 0,
                 "layer_failures": []}
        if self._app is None:
            return empty
        try:
            rows = self._app.execute(
                "SELECT verify FROM conversation_messages"
                " WHERE verify IS NOT NULL AND created_at >= ?",
                (cutoff_iso,)).fetchall()
        except sqlite3.Error:
            return empty

        turns = retries = degraded = first_pass = gate_errors = 0
        layers: Counter = Counter()
        for (raw,) in rows:
            try:
                vt = json.loads(raw)
            except (TypeError, ValueError):
                continue        # 脏数据不该让整个统计页 500
            turns += 1
            retries += vt.get("retries", 0) or 0
            if vt.get("degraded"):
                degraded += 1
            if vt.get("gate_error"):   # 校验器故障跳过校验的轮数：这些「通过」并非真通过
                gate_errors += 1
            if (vt.get("attempts") or 0) == 1 and vt.get("ok"):
                first_pass += 1
            for h in vt.get("history") or []:
                for name in h.get("failed") or []:
                    layers[name] += 1
        if turns == 0:
            return empty
        return {
            "turns": turns,
            "retries": retries,
            "avg_retries": round(retries / turns, 3),
            "degraded": degraded,
            "degraded_rate": round(degraded / turns, 3),
            "first_pass_rate": round(first_pass / turns, 3),
            # 因校验器自身故障而跳过校验的轮数（fail-open）：>0 说明有回答其实没被真校验过
            "gate_errors": gate_errors,
            # 哪层最爱拦：按次数降序，用于判断该调哪个分项开关/阈值
            "layer_failures": [{"layer": k, "zh": failed_layers_zh([k]), "count": v}
                               for k, v in layers.most_common()],
        }

    def _user_conv_ids(self, user_id: str | None) -> list[str]:
        """该用户的全部会话 id（对话记忆按 conv_id 归属，用它做用户隔离）。"""
        if self._app is None or not user_id:
            return []
        try:
            rows = self._app.execute(
                "SELECT id FROM conversations WHERE user_id=?", (user_id,)).fetchall()
        except sqlite3.Error:
            return []
        return [r[0] for r in rows]

    def _count_user_memories(self, user_id: str | None) -> int:
        conv_ids = self._user_conv_ids(user_id)
        if self._mem is None or not conv_ids:
            return 0
        ph = ",".join("?" * len(conv_ids))
        return self._scalar(
            self._mem,
            f"SELECT COUNT(*) FROM memory_records "
            f"WHERE kind='conversation' AND superseded=0 AND owner_id IN ({ph})",
            tuple(conv_ids))

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

    def memory_items(self, user_id: str | None, limit: int = 50) -> list[dict]:
        """列出当前用户从对话中沉淀的长期记忆（供首页「AI 记住的偏好」查看）。

        按会话归属做用户隔离：对话记忆以 conv_id 为 owner_id，只返回属于该用户
        会话的 conversation 记忆（排除已被取代的 superseded 记录）。记忆库缺失或
        用户无会话时返回空。
        """
        conv_ids = self._user_conv_ids(user_id)
        if self._mem is None or not conv_ids:
            return []
        limit = max(1, min(limit, 200))
        ph = ",".join("?" * len(conv_ids))
        try:
            rows = self._mem.execute(
                f"SELECT id, text, kind, created_at FROM memory_records "
                f"WHERE kind='conversation' AND superseded=0 AND owner_id IN ({ph}) "
                f"ORDER BY created_at DESC LIMIT ?",
                (*conv_ids, limit)).fetchall()
        except sqlite3.Error:
            return []
        return [{"id": r[0], "text": r[1], "collection": r[2], "created_at": r[3]} for r in rows]

    def delete_memory(self, user_id: str | None, mem_id: str) -> bool:
        """删除当前用户的一条对话记忆（按会话归属校验）。找不到/非本人/无写后端返回 False。"""
        if self._mem_store is None:
            return False
        recs = self._mem_store.get([mem_id])
        if not recs:
            return False
        # 归属校验：记忆的 owner_id 必须是该用户的某个会话（否则视为不存在，不泄露他人记忆）
        if recs[0].owner_id not in set(self._user_conv_ids(user_id)):
            return False
        self._mem_store.delete([mem_id])
        return True

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

    def _ops_section(self, agg, series, counts, gate, quality) -> dict:
        # 本区（AI 运行统计）**整片全局、不按用户切**：轨迹库 trajectory_events 本就没有
        # user_id 列切不了，gate/quality 若按用户切就会和同页其它指标对不上。
        # counts 须传 _global_counts() 的产物，别误传 _app_counts()（那是学习主场用的本人口径）。
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
                "avg_run_duration_ms": agg["avg_run_duration_ms"],
                "total_run_duration_ms": agg["total_run_duration_ms"],
                # 注意：这是 LLM 网络重试（超时/限流后重发请求），与交付门重答无关；
                # 后者见下面的 gate.retries。两者口径完全不同，别混着看。
                "retries": agg["retries"],
                "cost_usd": agg["cost_usd"],
                "cost_currency": self._currency,
                "conversations": counts["conversations"],
                "messages": counts["messages"],
            },
            "daily": series,
            "tools": self._tools_list(agg["tool_counts"], agg["tool_errors"]),
            "steps_histogram": self._steps_histogram(agg["steps_per_run"]),
            # 交付门：答案没过校验带反馈重答的统计（读 conversation_messages.verify 列）
            "gate": gate,
            # 轨迹 judge 的分层质量分（读 progress 列 scope=quality 项）
            "quality": quality,
        }

# app/stats.py
"""首页概览统计聚合层。

单一职责：把 harness 运行轨迹（harness.db / trajectory_events）与应用业务数据
（app.db / memory.db）聚合成首页需要的指标 dict。纯读、无副作用、不碰 HTTP，
可脱离 FastAPI 单测。

产出服务两种视角，作用域**不同**，故分别聚合：
- learn（学习主场，「AI 在为我做什么」）：把运行数据翻译成产品语言（AI 用过哪些能力、
  花了多少力气）+ 学习资产 —— 全部按 user_id 隔离，讲的是「我的」。
- ops（工程台 / AI 运行统计）：运维口径（成功率、P95 延迟、工具成功率、步数分布、交付门
  一次过率、轨迹 judge 质量分、全站会话数）—— **整片保持全库**，同一台机器上别人的运行
  也是运维对象；其中任何一项若按 user 切，就会和同页其它指标对不上。

轨迹库(harness.db)无 user_id，与应用库(app.db)是两个文件、无法 JOIN；learn 的隔离靠
conversations → conversation_runs → run_id 反查（见 _user_run_ids）。学习资产按 user_id 归属。
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from harness.usage import tiered_cost

from .verify import failed_layers_zh

# 统计口径统一按 UTC+8（北京时间）自然日切：「今日」= 北京今天 00:00 到现在，
# 「近 N 天」= 往前数 N-1 个自然日的 00:00 到现在。存储仍是 UTC（库内时间戳带 +00:00），
# 仅在此层把边界与分桶换算到 UTC+8。与 app/api/chat.py 的 _CN_TZ 同源。
_CN_TZ = timezone(timedelta(hours=8))

# 原始工具名 → 面向学习者的「能力」分组（图标, 标签, 归入的工具名集合）。
# 未列出的工具归入「其他能力」。顺序即展示顺序的兜底（实际按调用次数倒排）。
#
# 这张表会随工具集演进自然腐化，且腐化时不报错——只是所有调用悄悄堆进「其他能力」，
# 产品叙事失效。曾漏掉全部应用层工具（知识库/题库/考试）与全部 MCP 远程工具，
# 而 EXECUTOR_GUIDE 恰恰引导模型优先用 MCP 搜索工具，「联网查资料」因此注定接近 0。
# tests/app/test_stats_ability_groups.py 有护栏：表里的名字必须真实注册、且真实注册的
# 主要工具不得大面积落进「其他能力」。
_ABILITY_GROUPS: list[tuple[str, str, set[str]]] = [
    ("🌐", "联网查资料", {"http_request", "browse"}),
    ("💻", "运行代码", {"run_shell", "run_python", "run_java", "run_node"}),
    ("🧠", "记住你的偏好", {"remember", "recall_episodes", "search_memory"}),
    ("🔍", "检索知识库", {"search_knowledge"}),
    ("📥", "整理进知识库", {"save_to_knowledge"}),
    ("✏️", "出练习题", {"sample_questions", "generate_questions", "add_questions"}),
    ("📝", "考试与错题", {"start_exam", "save_wrong_answer", "sample_wrong_answers",
                      "delete_wrong_answers", "list_questions", "delete_questions"}),
    ("🧩", "拆解复杂任务", {"dispatch", "update_plan"}),
    ("✍️", "生成文件产物", {"write_file", "save_download"}),
    ("📄", "读你的资料", {"read_attachment", "list_attachments", "read_file", "list_files"}),
    ("🔢", "做计算", {"calculator"}),
]

# MCP 远程工具名形如 mcp__<server>__<tool>，由外部服务器决定、无法枚举，只能按名字里的
# 动作关键词归类。命中不了的仍进「其他能力」——那是诚实的，好过硬塞进某一类。
_MCP_KEYWORD_GROUPS: list[tuple[tuple[str, ...], str]] = [
    (("search", "搜索", "web_search"), "联网查资料"),
    (("fetch", "browse", "crawl", "抓取"), "联网查资料"),
]


def _ability_label(name: str) -> str | None:
    """工具名 → 能力标签；归不了类返回 None（调用方计入「其他能力」）。"""
    for _icon, label, names in _ABILITY_GROUPS:
        if name in names:
            return label
    if name.startswith("mcp__"):
        low = name.lower()
        for keys, label in _MCP_KEYWORD_GROUPS:
            if any(k in low for k in keys):
                return label
    return None


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


def _by_model_rows(by_model: dict, active: list | None = None) -> list[dict]:
    """把 {model: {...}} 转成按 total token 降序的列表，供前端分模型表格。

    active 非空时把没跑过的在用模型补成 0 行：配了却没用量本身就是信息——可能是这一档
    压根没被走到（如 judge 没接上、rerank 没开），零行看得见，缺行只会让人以为「统计漏了」。

    停用模型的剔除**不在这里**，在聚合入口就整条跳过（见 _aggregate 的 ModelUsage 分支），
    这样 tokens/成本/延迟等总计与本表同口径。此处仍做一次防御性过滤：调用方可能传入
    未经聚合过滤的 by_model（测试即如此），两处口径必须一致。
    """
    if active:
        allow = set(active)
        by_model = {k: v for k, v in by_model.items() if k in allow}
        for m in active:
            by_model.setdefault(m, {"prompt": 0, "completion": 0, "total": 0,
                                    "calls": 0, "cost": 0.0, "has_cost": False})
    rows = [{
        "model": name,
        "calls": m["calls"],
        "prompt": m["prompt"],
        "completion": m["completion"],
        "total_tokens": m["total"],
        "cost_usd": round(m["cost"], 6) if m["has_cost"] else None,   # ¥ 金额可能很小，多留精度
    } for name, m in by_model.items()]
    rows.sort(key=lambda r: r["total_tokens"], reverse=True)
    return rows


def _cn_day(created_at: str) -> str:
    """UTC ISO 时间戳 → UTC+8 自然日 (YYYY-MM-DD)；空串/解析失败返回空串。

    每日分桶的 key 据此生成，须与 _daily_series 用 now_cn.date() 拉出的日期序列对齐，
    否则趋势图当天数据会错位到相邻 UTC 日。裸时间戳（无偏移）按 UTC 处理，兜底不崩。
    """
    if not created_at:
        return ""
    try:
        dt = datetime.fromisoformat(created_at)
    except (ValueError, TypeError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_CN_TZ).date().isoformat()


# 分桶刻意把分辨率放在**高步数**端：这张图的用途是识别「绕圈跑飞」的运行，而跑飞的运行
# 步数异常地多（主聊天 max_steps 上限 100，编排器下按 StepStarted 逐子步累计，能到几十步）。
# 旧分桶 1/2/3/4/5-6/7+ 把分辨率全堆在低端，7+ 一个桶从 7 吃到 100——8 步的略长运行和 50 步的
# 彻底失控落进同一根柱，恰好分不开最该分开的那批。现改为低端合并、高端展开：健康运行不必逐步
# 区分，失控运行按 7-10（略长）/ 11-20（可疑）/ 21+（基本跑飞）拉开。桶数不变，前端无需改。
def _step_bucket(n: int) -> str:
    if n <= 1:
        return "1"
    if n <= 3:
        return "2-3"
    if n <= 6:
        return "4-6"
    if n <= 10:
        return "7-10"
    if n <= 20:
        return "11-20"
    return "21+"


_STEP_ORDER = ["1", "2-3", "4-6", "7-10", "11-20", "21+"]

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
                 maintainer=None,
                 price_tiers: list | None = None,
                 price_tiers_by_model: dict | None = None,
                 price_map: dict | None = None,
                 currency: str = "$",
                 active_models: list | None = None,
                 now=None) -> None:
        self._traj = trajectory_conn
        self._app = app_conn
        self._mem = memory_conn
        # 记忆的写侧后端（SqliteVecBackend）：删除要同步清 records/vec/fts 三表，
        # 走它才安全；只读统计仍用 _mem 连接。缺省 None 时删除不可用（优雅降级）。
        self._mem_store = memory_store
        # 记忆维护器（MemoryMaintainer）：手动「整理相似偏好」用；缺省 None 时该功能优雅降级。
        self._maintainer = maintainer
        # 分层单价表（按输入长度分档）；配置后由 token 数现算成本，可回溯历史事件。
        # 为空则回退累加事件里已存的 cost_usd（旧口径）。
        self._price_tiers = price_tiers or []
        # 按模型的分层表 {model: tiers} 与扁平价表 {model: [in,out]/1k}：让 stats 也按模型精确回溯成本
        self._price_tiers_by_model = price_tiers_by_model or {}
        self._price_map = price_map or {}
        self._currency = currency
        # 当前在用的模型（主/快速/judge/embedding/rerank，去重去空）。「分模型用量」只认这几个：
        # 轨迹事件是永久的，换过模型后旧模型的历史用量会一直挂在表里——实测某库里已停用的
        # qwen-turbo 占 119 万 token，稳居第一行，把在用模型全压下去，看着像它还在跑。
        # 为空时不过滤（保持旧行为），避免装配没传就把整张表清空。
        self._active_models = [m for m in dict.fromkeys(active_models or []) if m]
        self._active_set = set(self._active_models)   # 逐事件过滤，用集合避免线性查找
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _model_cost(self, prompt: int, completion: int, model: str) -> float | None:
        """按模型回溯成本：扁平 price_map（per-model）→ 该模型分层表 → 全局默认分层表。都无则 None。"""
        price = self._price_map.get(model)
        if price:
            in_1k, out_1k = price
            return prompt / 1000 * in_1k + completion / 1000 * out_1k
        tiers = self._price_tiers_by_model.get(model) or self._price_tiers
        if tiers:
            return tiered_cost(prompt, completion, tiers)
        return None

    # ---------- 公开入口 ----------

    def overview(self, user_id: str | None, days: int = 14) -> dict:
        days = max(1, min(days, 90))
        now = self._now()
        if now.tzinfo is None:                       # 约定注入 aware datetime；裸值兜底按 UTC
            now = now.replace(tzinfo=timezone.utc)
        # 按 UTC+8 自然日切窗：起点 = 北京今天 00:00 再回退 (days-1) 天（days=1 即今天 00:00）。
        # 起点转回 UTC ISO 传给 SQL —— 库内时间戳同为 +00:00，字典序比较才等价于时间序。
        now_cn = now.astimezone(_CN_TZ)
        start_cn = (now_cn.replace(hour=0, minute=0, second=0, microsecond=0)
                    - timedelta(days=days - 1))
        cutoff = start_cn.astimezone(timezone.utc)
        # 一次读盘、两份聚合：learn（「AI 在为我做什么」）按 user 隔离，ops（工程台的运维
        # 口径：全局成功率/延迟/工具健康度）仍看全库——同一台机器上别人的运行也是运维对象。
        events = self._load_events(cutoff.isoformat())
        agg = self._aggregate(events)
        # 趋势序列按 UTC+8 日期拉：传 now_cn，末项即北京今天，与 _cn_day 的分桶 key 对齐。
        series = self._daily_series(agg["daily"], now_cn, days)
        user_runs = self._user_run_ids(user_id)
        user_events = [e for e in events if e[0] in user_runs]
        user_agg = self._aggregate(user_events)
        user_series = self._daily_series(user_agg["daily"], now_cn, days)
        app_counts = self._app_counts(user_id)
        return {
            "range_days": days,
            # 学习主场：本人的运行 + 本人的资产
            "learn": self._learn_section(user_id, user_agg, user_series, app_counts,
                                         cutoff.isoformat()),
            # AI 运行统计：运维口径，整片全局、不按用户切（含 gate/quality/会话数）
            "ops": self._ops_section(agg, series, self._global_counts(),
                                     self._gate_stats(cutoff.isoformat()),
                                     self._quality_section(
                                         self._load_progress_rows(cutoff.isoformat())),
                                     self._context_stats(cutoff.isoformat())),
        }

    # ---------- 轨迹聚合 ----------

    def _user_run_ids(self, user_id: str | None) -> set[str]:
        """该用户全部会话的 run_id —— 轨迹事件按用户隔离的唯一抓手。

        轨迹库(harness.db)无 user_id，且与应用库(app.db)是两个 SQLite 文件、无法 JOIN，
        故先在应用库取 run_id 集合，再据此过滤轨迹事件（在 Python 侧过滤而非 SQL IN：
        run_id 可能上千，会撞 SQLite 变量个数上限）。

        覆盖完整、不会漏算：conversation_runs 由 chat.py 三处 add_run 写入，覆盖全部顶层
        run；子 agent(dispatch) 的循环事件由 DispatchTool 内部消费、不进 trajectory_events。
        """
        if self._app is None or not user_id:
            return set()
        try:
            rows = self._app.execute(
                "SELECT run_id FROM conversation_runs WHERE conv_id IN "
                "(SELECT id FROM conversations WHERE user_id=?)", (user_id,)).fetchall()
        except sqlite3.Error:
            return set()
        return {r[0] for r in rows}

    def _load_events(self, cutoff_iso: str,
                     run_ids: set[str] | None = None) -> list[tuple[str, str, str, dict]]:
        """区间内的轨迹事件。run_ids 非 None 时只保留这些 run（按用户隔离）。"""
        try:
            rows = self._traj.execute(
                "SELECT run_id, type, created_at, data FROM trajectory_events "
                "WHERE created_at >= ? ORDER BY created_at", (cutoff_iso,)).fetchall()
        except sqlite3.Error:
            return []
        out = []
        for run_id, typ, created_at, data in rows:
            if run_ids is not None and run_id not in run_ids:
                continue
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
        by_model: dict[str, dict] = {}   # {模型名: {prompt,completion,total,calls,cost,has_cost}}
        tool_name_by_id: dict[str, str] = {}
        tool_counts: Counter = Counter()
        tool_errors: Counter = Counter()
        steps_per_run: Counter = Counter()
        daily: dict[str, dict] = defaultdict(lambda: {"runs": 0, "tokens": 0})
        run_start: dict[str, str] = {}
        run_end: dict[str, str] = {}

        for run_id, typ, created_at, d in events:
            # day（UTC+8 自然日）只在 RunStarted/ModelUsage 两个分支用到，惰性算，
            # 避免为占绝大多数的 TextDelta/ReasoningDelta 事件白解析时间戳。
            if typ == "RunStarted":
                runs_started.add(run_id)
                if created_at:
                    run_start[run_id] = created_at
                day = _cn_day(created_at)
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
                # 停用模型整条跳过：轨迹事件永久保留，换过模型后旧模型的用量会一直计进来。
                # 在这里拦而不是只过滤展示行，是为了让 tokens / 成本 / 调用次数 / 延迟 / 重试
                # 与「分模型用量」同口径——否则各行之和对不上总计，看着像统计出了错。
                # active 为空时不过滤（装配没传就退回旧行为，不至于把统计清空）。
                if self._active_models and (d.get("model") or "(未知)") not in self._active_set:
                    continue
                model_calls += 1
                u = d.get("usage", {}) or {}
                total_prompt += u.get("prompt", 0) or 0
                total_completion += u.get("completion", 0) or 0
                tok = u.get("total", 0) or 0
                total_tokens += tok
                day = _cn_day(created_at)
                if day:
                    daily[day]["tokens"] += tok
                # 只收真实测过的耗时：embedding（memory/embeddings.py）与 rerank
                # （memory/reranker.py）上报的 ModelUsage 把 latency_ms 硬编码成 0.0，
                # 收进来会把均值/p95 系统性拉低——检索用得越多显得越快，与直觉相反。
                lat = d.get("latency_ms")
                if isinstance(lat, (int, float)) and lat > 0:
                    latencies.append(float(lat))
                if (d.get("attempts") or 1) > 1:
                    retries += 1
                # 按模型计价（可回溯）：per-model 扁平价/分层表 → 全局默认分层表 → 事件里存的 cost_usd
                p_, c_ = u.get("prompt", 0) or 0, u.get("completion", 0) or 0
                model = d.get("model") or "(未知)"
                mc = self._model_cost(p_, c_, model)
                if mc is None:
                    cost = d.get("cost_usd")
                    mc = float(cost) if isinstance(cost, (int, float)) else None
                bm = by_model.setdefault(model, {"prompt": 0, "completion": 0, "total": 0,
                                                 "calls": 0, "cost": 0.0, "has_cost": False})
                bm["prompt"] += p_; bm["completion"] += c_; bm["total"] += tok; bm["calls"] += 1
                if mc is not None:
                    bm["cost"] += mc; bm["has_cost"] = True
                    any_cost = True
                    total_cost += mc
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
            "by_model": by_model,
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
        # 先按工具名逐个归类（含 MCP 关键词兜底），再按标签汇总——这样 MCP 工具也能进组
        by_label: Counter = Counter()
        other = 0
        for name, cnt in tool_counts.items():
            label = _ability_label(name)
            if label is None:
                other += cnt
            else:
                by_label[label] += cnt
        for icon, label, _names in _ABILITY_GROUPS:
            c = by_label.get(label, 0)
            if c > 0:
                out.append({"icon": icon, "label": label, "count": c})
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

    def _context_stats(self, cutoff_iso: str) -> dict:
        """分层上下文统计，读 conversation_messages.context 列。**全局**，与同页其它运维指标同口径。

        最要紧的是 amnesia_turns：被挤出 L1 的历史 >0 但 L2 摘要没成的轮数 —— 那些轮模型是
        真丢了一段历史，还照着残缺上下文自信作答了。这个数只要不是 0 就得查。
        它与 summary_errors 不同：摘要失败但本就没挤出东西（evicted=0）无害，不该算进来。
        """
        empty = {"turns": 0, "layered_turns": 0, "evicted_total": 0, "amnesia_turns": 0,
                 "summary_errors": 0, "retrieval_errors": 0, "summary_ok": 0}
        if self._app is None:
            return empty
        try:
            rows = self._app.execute(
                "SELECT context FROM conversation_messages"
                " WHERE context IS NOT NULL AND created_at >= ?",
                (cutoff_iso,)).fetchall()
        except sqlite3.Error:
            return empty

        turns = layered = evicted_total = amnesia = s_err = r_err = s_ok = 0
        for (raw,) in rows:
            try:
                ct = json.loads(raw)
            except (TypeError, ValueError):
                continue        # 脏数据不该让整个统计页 500
            turns += 1
            if ct.get("strategy") != "layered":
                continue
            layered += 1
            evicted = ct.get("evicted") or 0
            evicted_total += evicted
            if ct.get("summary") == "error":
                s_err += 1
                if evicted > 0:      # 挤出去的没被摘到 → 这一轮真失忆了
                    amnesia += 1
            elif ct.get("summary") == "ok":
                s_ok += 1
            if ct.get("retrieval") == "error":
                r_err += 1
        if turns == 0:
            return empty
        return {"turns": turns, "layered_turns": layered, "evicted_total": evicted_total,
                # >0 即有回答是在「丢了一段历史且模型不自知」的情况下给出的
                "amnesia_turns": amnesia,
                "summary_errors": s_err, "retrieval_errors": r_err, "summary_ok": s_ok}

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

    def _recent_downloads(self, user_id: str | None, cutoff_iso: str) -> list[dict]:
        # 跟随右上角时间范围：只列窗口内（按 UTC+8 自然日切）生成的产物；
        # 窗口内无产物时返回空，由前端给出「该时段还没有产物生成」的提示。
        if self._app is None:
            return []
        try:
            rows = self._app.execute(
                "SELECT id, filename, content_type, size, created_at FROM downloads "
                "WHERE user_id=? AND created_at >= ? "
                "ORDER BY seq DESC LIMIT 3", (user_id, cutoff_iso)).fetchall()
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
        limit = max(1, min(limit, 1000))
        ph = ",".join("?" * len(conv_ids))
        try:
            rows = self._mem.execute(
                f"SELECT id, text, kind, mem_type, created_at FROM memory_records "
                f"WHERE kind='conversation' AND superseded=0 AND owner_id IN ({ph}) "
                f"ORDER BY created_at DESC LIMIT ?",
                (*conv_ids, limit)).fetchall()
        except sqlite3.Error:
            return []
        return [{"id": r[0], "text": r[1], "collection": r[2], "mem_type": r[3],
                 "created_at": r[4]} for r in rows]

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

    def delete_memories(self, user_id: str | None, ids: list[str]) -> list[str]:
        """批量删除当前用户的对话记忆（逐条按会话归属校验）。返回实际删除的 id 列表；
        非本人/不存在的跳过。无写后端或空入参返回空。"""
        if self._mem_store is None or not ids:
            return []
        owned = set(self._user_conv_ids(user_id))
        recs = self._mem_store.get(list(ids))
        deletable = [r.id for r in recs if r.owner_id in owned]
        if deletable:
            self._mem_store.delete(deletable)
        return deletable

    async def consolidate_memories(self, user_id: str | None) -> dict:
        """手动「整理相似偏好」：把用户各会话里同主题的多条 semantic 偏好合并成一条。
        返回合并统计 {clusters, merged, created}。无维护器时优雅降级为全 0。"""
        total = {"clusters": 0, "merged": 0, "created": 0}
        if self._maintainer is None:
            return total
        for conv_id in self._user_conv_ids(user_id):
            try:
                r = await self._maintainer.consolidate_semantic(conv_id, "conversation")
            except Exception:                       # 单会话失败不影响其余（best-effort）
                continue
            for k in total:
                total[k] += r.get(k, 0)
        return total

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

    def _learn_section(self, user_id, agg, series, app_counts, cutoff_iso) -> dict:
        return {
            "assets": {
                "documents": app_counts["documents"],
                "memory": app_counts["memory"],
                "questions": app_counts["questions"],
                "wrong_answers": app_counts["wrong_answers"],
            },
            "conversations": app_counts["conversations"],
            "messages": app_counts["messages"],
            "recent_downloads": self._recent_downloads(user_id, cutoff_iso),
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

    def _ops_section(self, agg, series, counts, gate, quality, context) -> dict:
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
            # 分模型明细：token/调用次数/成本按模型拆开，供前端表格展示（totals 是全部模型的汇总）。
            # 含 embedding/rerank：它们的 emit 用量已经 _merged 并入主流落 trajectory（见 chat.py pump）。
            "by_model": _by_model_rows(agg["by_model"], self._active_models),
            "daily": series,
            "tools": self._tools_list(agg["tool_counts"], agg["tool_errors"]),
            "steps_histogram": self._steps_histogram(agg["steps_per_run"]),
            # 交付门：答案没过校验带反馈重答的统计（读 conversation_messages.verify 列）
            "gate": gate,
            # 轨迹 judge 的分层质量分（读 progress 列 scope=quality 项）
            "quality": quality,
            # 分层上下文：L1 挤出多少、L2/L3 成没成（读 conversation_messages.context 列）。
            # 看 amnesia_turns —— 不为 0 说明有回答是在丢了历史且模型不自知的情况下给出的。
            "context": context,
        }

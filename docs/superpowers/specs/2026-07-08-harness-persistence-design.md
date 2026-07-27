> ⚠️ **历史设计记录（已过时）**：harness 内核已抽成外部包
> [ai-harness-framework](https://github.com/sumengnan/ai-harness-framework)（import 名仍是 `harness`）。
> 本文是带日期的设计存档，文中的 `src/harness/` 路径与打包配置反映**当时**的仓库结构、未随抽包更新；
> 当前结构以 [架构文档](../../architecture-harness.md) 为准。

# AI Harness 持久化（子项目③d）设计规格

- **日期**：2026-07-08
- **状态**：待实现（brainstorming 已定稿）
- **定位**：单用户自用工具，harness 最后一块，在①②③a③b③c 之上扩展
- **前置**：①②③a③b-1③b-2③c③a-follow 已完成并在 `main`

---

## 0. 背景与范围

harness 收官：持久化。两块——**轨迹 sink**（事件流落 SQLite，供未来 LLM-as-judge/轨迹评测）+ **checkpoint 断点续跑**（`RunState` 序列化 + `AgentLoop.resume`）。②规格里把 `SQLiteTrajectorySink` 和"RunState 可序列化→断点续跑"列为此处扩展点。

**存储/可观测性**：SQLite（标准库 `sqlite3`）/ 沿用 OpenTelemetry。

设计通则：严格 YAGNI；轨迹 sink 零改 loop（事件订阅者）；checkpoint 抽 `_run_from` 共用、`run` 外部行为不变；测试 `:memory:` + mock，不打网络。

---

## 1. 范围与验收

### IN
- **序列化**（`persistence/serialize.py`）：`RunState`/`Message`/`ToolCall` ↔ dict 往返（checkpoint 用）；`Event` → dict 单向（轨迹用）。
- **轨迹 sink**：`TrajectoryStore`（SQLite）+ `TrajectorySink`（事件流包装器，run_id 从 `RunStarted` 捕获）。零改 loop。
- **checkpoint 断点续跑**：`CheckpointStore`（SQLite）；loop 注入可选 `checkpoint_store`，每步边界存快照；`AgentLoop.resume(run_id)` 从 `step+1` 继续。

### OUT（预留扩展点）
`ReplayModelClient`（从轨迹回放模型响应）、event→Event 反序列化、无状态 runtime/队列/水平扩容、跨机分布式 checkpoint、预算跨 resume 持久化、轨迹评测/LLM-judge 本身（③d 只产数据）。

### 验收标准
1. `RunState`（含 tool_calls）序列化往返相等。
2. `TrajectorySink.wrap` 包事件流 → 每事件按序入 SQLite；`load(run_id)` 取回有序。
3. loop 注入 `checkpoint_store` 跑完 → 每步边界有快照；不注入则不 checkpoint（**向后兼容，①现有测试不受影响**）。
4. **断点续跑**：预置 step=1 含历史消息的 checkpoint → `resume(run_id)` 从 step 2 继续、产出 `RunFinished`、恢复的历史在上下文；不存在的 run_id → `RunError`。
5. `RunFinished` 后快照被删；`RunError` 后保留。
6. ①②③ 原有测试不回归；无新依赖。

---

## 2. 架构与模块

**设计取向**：轨迹 sink 走事件订阅者（同 EpisodeRecorder）；checkpoint 需 loop 集成——把步循环抽成 `_run_from(state)` 供 `run`/`resume` 共用，`run` 外部行为**完全不变**（`checkpoint_store` 默认 None）。序列化独立模块，两处共用。

```
src/harness/persistence/           [新增]
├── __init__.py
├── serialize.py     RunState/Message/ToolCall ↔ dict; event_to_dict（单向）
├── trajectory.py    TrajectoryStore(SQLite) + TrajectorySink
└── checkpoint.py    CheckpointStore(SQLite)
src/harness/loop/agent_loop.py     [改] 抽 _run_from；run/resume 共用；步边界 checkpoint
src/harness/config.py              [改] persistence_db_path
```

**loop 改动（唯一改动，向后兼容）**：
- `__init__` 加可选 `checkpoint_store=None`。
- `run(user_message)`：建 state、append user → `_run_from(state, resuming=False)`（行为同现在）。
- `resume(run_id)`：`load(run_id)` → 无则 `RunError`；有则 `_run_from(state, resuming=True)`。
- `_run_from`：步循环 `range(state.step+1, max_steps+1)`；`StepFinished` 后 `save(state)`；`RunFinished` 时 `delete(run_id)`。

**依赖新增**：无（标准库 `sqlite3` + `json`）。

**边界**：
- `serialize` 纯函数，独立单测。
- `TrajectoryStore`/`CheckpointStore` 只认 dict/RunState + SQLite。
- `TrajectorySink` 是 loop 旁路订阅者。
- checkpoint 内部集成，但 `_run_from` 抽取保证 `run` 行为零变化。

---

## 3. serialize（`persistence/serialize.py`）

**往返（无损）**：
```python
def toolcall_to_dict(tc) -> dict          # {id, name, arguments}
def toolcall_from_dict(d) -> ToolCall
def message_to_dict(m) -> dict            # {role: role.value, content, tool_calls: [...], tool_call_id}
def message_from_dict(d) -> Message       # Role(d["role"]) 还原枚举
def runstate_to_dict(s) -> dict           # {run_id, step, messages: [...]}
def runstate_from_dict(d) -> RunState
```

**单向（轨迹）**：
```python
def event_to_dict(ev) -> dict:            # {"type": 类名, "data": {…}}
    # TextDelta→{text}；StepStarted/StepFinished→{step}；RunStarted→{run_id}；
    # ToolCallRequested→{tool_calls:[toolcall_to_dict]}；ToolStarted→{tool_call:...}；
    # ToolFinished→{result:{tool_call_id,content,is_error}}；RunFinished→{message:message_to_dict}；
    # RunError→{error}；ModelUsage→{usage:{prompt,completion,total},cost_usd,attempts,latency_ms}
```
- 纯函数；`Message`/`ToolCall`/`RunState` 往返相等是 checkpoint 正确性基石。

---

## 4. 轨迹 sink（`persistence/trajectory.py`）

**`TrajectoryStore`**（SQLite）：
```sql
CREATE TABLE IF NOT EXISTS trajectory_events(
  run_id TEXT, seq INTEGER, type TEXT, data TEXT, created_at TEXT,
  PRIMARY KEY(run_id, seq));
```
`append(run_id, seq, event_dict)`（`data=json.dumps(event_dict)`）/ `load(run_id) -> list[dict]`（按 seq 有序）。

**`TrajectorySink`**（事件流包装器，零改 loop）：
```python
async def wrap(self, events):
    run_id, seq = None, 0
    async for ev in events:
        if isinstance(ev, RunStarted):
            run_id = ev.run_id
        if run_id is not None:
            self._store.append(run_id, seq, event_to_dict(ev)); seq += 1
        yield ev
```
- 用法：`async for ev in sink.wrap(loop.run(task))` —— 透传给 UI + 完整轨迹落库，供未来评测读取。

---

## 5. checkpoint + resume（`persistence/checkpoint.py` + loop 改动）

**`CheckpointStore`**（SQLite）：
```sql
CREATE TABLE IF NOT EXISTS checkpoints(
  run_id TEXT PRIMARY KEY, state TEXT, step INTEGER, updated_at TEXT);
```
`save(state)`（`INSERT OR REPLACE`，`state=json.dumps(runstate_to_dict(state))`）/ `load(run_id) -> RunState | None`（`runstate_from_dict`）/ `delete(run_id)`。

**loop 改动**（抽 `_run_from`，`run` 行为不变）：
```python
async def run(self, user_message):
    state = RunState(run_id=self._new_run_id())
    state.append(Message(role=Role.USER, content=user_message))
    async for ev in self._run_from(state, resuming=False): yield ev

async def resume(self, run_id):
    if self._checkpoint_store is None:
        yield RunError(error="未配置 checkpoint_store"); return
    state = self._checkpoint_store.load(run_id)
    if state is None:
        yield RunError(error=f"无 checkpoint：{run_id}"); return
    async for ev in self._run_from(state, resuming=True): yield ev

async def _run_from(self, state, resuming):
    if self._budget: self._budget.start()               # 幂等
    if not resuming: yield RunStarted(run_id=state.run_id)
    with self._tracer.start_as_current_span("run") as run_span:
        run_span.set_attribute("harness.run_id", state.run_id)
        for step in range(state.step + 1, self._max_steps + 1):   # fresh:1..；resume:step+1..
            state.step = step
            … 现有步体（预算检查 / model_call / 工具执行 / ModelUsage），完全不变 …
            # 无工具调用分支：yield RunFinished(assistant); 
            #   if self._checkpoint_store: self._checkpoint_store.delete(state.run_id); return
            yield StepFinished(step=step)
            if self._checkpoint_store:
                self._checkpoint_store.save(state)      # 步边界存快照
        yield RunError(error=f"达到 max_steps 上限 ({self._max_steps})")
```

- **续跑语义**：中断在某步中途 → 最新快照是上一个完整步 → resume 从下一步重跑（重跑一次模型调用可接受）。
- **删快照**：`RunFinished` 删（跑完无需续）；`RunError` 保留（可再 resume）。
- **向后兼容**：`checkpoint_store=None` 不存不删，`run` 事件序列与现在**一致**，①现有 loop 测试不受影响。
- **预算跨 resume**：v1 用新 `BudgetTracker`（不持久化预算，OUT）。

---

## 6. 配置 / 测试 / 依赖

### 新增配置
```
persistence_db_path: str = "harness.db"
```

### 测试策略（`:memory:` SQLite + mock 模型，不打网络）
- `serialize`：`RunState`（含 tool_calls 的 assistant + tool 消息）往返字段相等；`event_to_dict` 对 `TextDelta`/`ToolFinished`/`RunFinished`/`ModelUsage` 产出正确 `{type,data}`。
- `TrajectoryStore`：`append` 多条 → `load` 按 seq 有序。
- `TrajectorySink.wrap`：包以 `RunStarted` 开头的事件流 → 全部入库、`load(run_id)` 取回。
- `CheckpointStore`：`save`→`load` 往返；`load` 不存在→`None`；`delete` 后取不到。
- **loop checkpoint**：记录型 fake store 断言每步边界 `save`（step 递增）；真 store 下 `RunFinished` 后快照删。
- **断点续跑**：手工 `save` 一个 step=1、含历史消息的 `RunState` → `resume(run_id)`（mock 给 step2 最终答案）→ `RunFinished`、恢复历史在上下文；`resume` 不存在的 run_id → `RunError`。
- **向后兼容**：`checkpoint_store=None` 的 `run` 事件序列与现状一致（现有测试 + 一条显式无 store 测试）。

### 依赖新增
无（标准库 `sqlite3` + `json`）。

---

## 7. 后续衔接（备忘，非本次范围）

- **ReplayModelClient**：从轨迹回放模型响应，把真实 run 变可调试/回归用例。
- **轨迹评测 / LLM-as-judge**：读 `TrajectoryStore` 的轨迹打分，接 CI 门禁（企业级第 16 块）。
- **企业级**：无状态 runtime + 队列 + 水平扩容（session 状态外置，checkpoint 是基础）；预算跨 resume 持久化。
- App 层：长任务（大文件入库、模拟考试生成）中断可 `resume` 续跑。

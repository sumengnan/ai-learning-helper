> ⚠️ **历史设计记录（已过时）**：harness 内核已抽成外部包
> [ai-harness-framework](https://github.com/sumengnan/ai-harness-framework)（import 名仍是 `harness`）。
> 本文是带日期的设计存档，文中的 `src/harness/` 路径与打包配置反映**当时**的仓库结构、未随抽包更新；
> 当前结构以 [架构文档](../../architecture-harness.md) 为准。

# AI Harness 情景记忆（子项目③a-follow）设计规格

- **日期**：2026-07-08
- **状态**：待实现（brainstorming 已定稿，用户预批准"全部完成再审"）
- **定位**：单用户自用工具，在①②③a③b③c 之上扩展
- **前置**：①②③a③b-1③b-2③c 已完成并在 `main`

---

## 0. 背景与范围

在 ③a 记忆/RAG 的向量设施之上加"过往任务经验"（情景记忆）：一次任务完成后把 `{任务, 结果, 成败}` 存成一条 episode，之后遇到相似任务时检索出来参考。**纯复用 ③a 的 `Memory`/`MemoryStore`/`EmbeddingClient`**（collection 感知），新增 `episodes` collection + 捕获/检索逻辑。

**存储/可观测性**：沿用 SQLite+sqlite-vec / OpenTelemetry。

设计通则：严格 YAGNI；零改 loop / ContextManager / MemoryStore；捕获=事件流包装器（同②订阅路子）、检索/写入=普通工具（同 ③a）；成败用启发式；测试复用 mock embedder + `:memory:` 不打网络。

---

## 1. 范围与验收

### IN
- `Episode`（`{task, outcome, success}` + `to_text()`）。
- `EpisodicMemory`（`Memory` 薄门面，绑定 `episodes` collection）：`record` / `recall`。
- `EpisodeRecorder`（事件流包装器）：`wrap(loop.run(task), task)` 透传事件，run 终止时自动记 episode（`RunFinished`→成功、`RunError`→失败），**loop 零改动**。
- 两工具：`recall_episodes(query, k)` + `record_episode(task, lesson, success)`。

### OUT（预留扩展点）
LLM 摘要"做法"、LLM-judge 判成败、任务开始自动注入（ContextManager 扩展点）、经验去重/衰减/评分、遗忘策略。

### 验收标准
1. `EpisodicMemory.record(...)` 后 `recall` 相似 query 能召回并按相似度排序（mock embedder + `:memory:`）。
2. `EpisodeRecorder.wrap` 包 `RunFinished` 事件流 → 结束后落一条 `success=True` 的 episode；`RunError` → `success=False`；未见终止事件（消费者提前中断）→ 不记录。
3. `record_episode` 工具写入后可被 `recall_episodes` 召回。
4. collection 隔离：episodes 不在查 `knowledge` 时被召回（反之亦然）。
5. 集成：`AgentLoop` 被 recorder 包裹跑完 → episode 自动入库；另一 run 的 `recall_episodes` 工具取到（mock 模型 + mock embedder）。
6. ③a 及以往测试不回归。

---

## 2. 架构与模块

**设计取向**：纯复用 ③a——`EpisodicMemory` 是绑定 `episodes` collection 的 `Memory` 门面 + `Episode` 序列化；捕获走事件流包装器；检索/写入走普通工具。零改 loop / ContextManager / MemoryStore。

```
src/harness/memory/episodic.py               [新增] Episode + EpisodicMemory + EpisodeRecorder
src/harness/tools/builtins/episode_tools.py  [新增] RecallEpisodesTool + RecordEpisodeTool
src/harness/config.py                        [改] episode_collection、episode_recall_k
```

**数据流**：
- 写（自动）：`async for ev in recorder.wrap(loop.run(task), task)` → 透传给 UI → run 终止时 `EpisodicMemory.record(task, 最终答案/错误, 成败)`。
- 写（显式）：模型发 `record_episode({task, lesson, success})` → `record`。
- 读：模型发 `recall_episodes({query, k})` → `recall` → 命中经验回填，指导当前任务。

**依赖新增**：无（复用 ③a `Memory`/`EmbeddingClient`/`MemoryStore`）。

**边界**：
- `Episode`/`EpisodicMemory` 纯数据 + 门面，独立单测。
- `EpisodeRecorder` 是 loop 的旁路订阅者（透传 + 终止时记一条）。
- 两工具与 ③a `search_memory`/`remember` 同构（持有 `EpisodicMemory`）。

---

## 3. Episode + EpisodicMemory

```python
@dataclass
class Episode:
    task: str
    outcome: str
    success: bool

    def to_text(self) -> str:
        status = "成功" if self.success else "失败"
        return f"任务：{self.task}\n结果（{status}）：{self.outcome}"


class EpisodicMemory:
    def __init__(self, memory: Memory, collection: str = "episodes") -> None:
        self._memory = memory
        self._collection = collection

    async def record(self, task: str, outcome: str, success: bool) -> list[int]:
        ep = Episode(task, outcome, success)
        return await self._memory.add_texts(
            [ep.to_text()], self._collection, {"success": success, "task": task[:200]})

    async def recall(self, query: str, k: int) -> list:      # list[MemoryHit]
        return await self._memory.search(query, self._collection, k)
```

- 复用 `Memory.add_texts`/`search`（含分块、embed、sqlite-vec KNN、collection 隔离），零重造。

---

## 4. EpisodeRecorder（事件流包装器）

```python
class EpisodeRecorder:
    def __init__(self, episodic: EpisodicMemory) -> None:
        self._episodic = episodic

    async def wrap(self, events, task: str):
        outcome, success, terminal = "", False, False
        async for ev in events:
            if isinstance(ev, RunFinished):
                outcome, success, terminal = ev.message.content or "", True, True
            elif isinstance(ev, RunError):
                outcome, success, terminal = ev.error, False, True
            yield ev
        if terminal:                 # 只记录跑完（有终止事件）的 run
            await self._episodic.record(task, outcome, success)
```

- **loop 零改动**：`recorder.wrap(loop.run(task), task)` 是外层旁路，透传全部事件给 UI，末尾落库。
- **成败启发式**：`RunFinished`=成功 / `RunError`=失败（LLM-judge 延后）。
- 消费者提前 break（未见终止事件）→ `terminal=False` → 不记录不完整的 run。

---

## 5. 工具（recall_episodes / record_episode）

```python
class RecallEpisodesTool(Tool):
    name = "recall_episodes"
    description = "检索过往相似任务的经验（做法与成败），参考它来完成当前任务。"
    class Params(BaseModel):
        query: str
        k: int | None = None
    def __init__(self, episodic: EpisodicMemory, default_k: int = 3): ...
    async def run(self, p) -> str:
        hits = await self._episodic.recall(p.query, p.k if p.k is not None else self._default_k)
        if not hits: return "（无相关历史经验）"
        return "\n\n".join(f"[{i}] {h.text}" for i, h in enumerate(hits, 1))


class RecordEpisodeTool(Tool):
    name = "record_episode"
    description = "把一次任务的经验（做法/教训与成败）记录下来供以后参考。"
    class Params(BaseModel):
        task: str
        lesson: str
        success: bool = True
    def __init__(self, episodic: EpisodicMemory): ...
    async def run(self, p) -> str:
        await self._episodic.record(p.task, p.lesson, p.success)
        return "已记录经验。"
```

- 自动继承②：工具执行进 `tool_call` span、错误回填、截断。
- 注册：同一 `EpisodicMemory` 注入两工具、`registry.register(...)`。

---

## 6. 配置 / 测试 / 依赖

### 新增配置（`config.py`）
```
episode_collection: str = "episodes"
episode_recall_k: int = 3
```

### 测试策略（mock embedder + `:memory:`，不打网络）
- `Episode.to_text`：成功/失败文案。
- `EpisodicMemory`：`record`→`recall` 往返召回。
- `EpisodeRecorder.wrap`：喂 `[TextDelta?, RunFinished]` 事件流 → 透传且末尾落一条 `success=True`；`RunError` → `success=False`；无终止事件 → 不记录。
- 工具：`RecordEpisodeTool` 写 → `RecallEpisodesTool` 召回 往返（经 `ToolExecutor`）。
- collection 隔离：episodes 不在查 `knowledge` 时召回。
- 集成：`AgentLoop` 被 `recorder.wrap` 包裹跑完 → 断言 episode 入库（recall 到）；另一 run 里 `recall_episodes` 工具取到。

### 依赖新增
无（纯复用 ③a）。

---

## 7. 后续衔接（备忘，非本次范围）

- **任务开始自动注入**：`ContextManager.build()` 挂 `EpisodicMemory.recall`，run 开始按首条用户消息注入 top-k 经验（①预留扩展点）。
- **LLM 摘要/判成败**：用便宜模型把轨迹摘成"做法"、判真实成败（比启发式准）。
- **经验治理**：去重、时间衰减、成功率评分、遗忘。
- **③d 持久化**：全轨迹 sink → 更丰富的 episode 来源。

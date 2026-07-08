# AI Harness 情景记忆（子项目③a-follow）实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在 ③a 向量设施上加"过往任务经验"——`Episode` + `EpisodicMemory`（绑定 episodes collection）+ `EpisodeRecorder`（事件流包装器，run 完成自动记）+ `recall_episodes`/`record_episode` 两工具。

**架构：** `memory/episodic.py` 纯复用 ③a `Memory`；捕获=事件流包装器（loop 零改动）；检索/写入=普通工具。

**技术栈：** 沿用 · **无新依赖**。

**规格：** `docs/superpowers/specs/2026-07-08-harness-episodic-memory-design.md`

**提交规范：** git 身份已是 sumengnan，默认提交；**任何 commit message 不得出现 Claude/AI/Co-Authored-By 等署名**。

---

## 文件结构与职责

| 文件 | 动作 | 职责 |
|---|---|---|
| `src/harness/memory/episodic.py` | 新增 | `Episode` + `EpisodicMemory` + `EpisodeRecorder` |
| `src/harness/tools/builtins/episode_tools.py` | 新增 | `RecallEpisodesTool` + `RecordEpisodeTool` |
| `src/harness/config.py` | 改 | `episode_collection`、`episode_recall_k` |

---

## 任务 0：配置

**文件：** 改 `src/harness/config.py`、测试 `tests/test_config.py`

- [ ] **步骤 1：写失败测试**（`tests/test_config.py` 追加）

```python
def test_episodic_defaults():
    cfg = HarnessConfig(api_key="k")
    assert cfg.episode_collection == "episodes"
    assert cfg.episode_recall_k == 3
```

运行：`uv run pytest tests/test_config.py::test_episodic_defaults -v`　预期 FAIL。

- [ ] **步骤 2：改 `src/harness/config.py`** 末尾追加：

```python
    # 情景记忆
    episode_collection: str = "episodes"
    episode_recall_k: int = 3
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/test_config.py -v`　预期全 pass。
```bash
git add src/harness/config.py tests/test_config.py
git commit -m "chore: 情景记忆配置项"
```

---

## 任务 1：Episode + EpisodicMemory + EpisodeRecorder

**文件：** 创建 `src/harness/memory/episodic.py`、测试 `tests/test_episodic.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/test_episodic.py
from harness.memory.episodic import Episode, EpisodicMemory, EpisodeRecorder
from harness.memory.memory import Memory
from harness.memory.store import MemoryStore
from harness.events import RunFinished, RunError, TextDelta
from harness.types import Message, Role


def _episodic(mock_embedder):
    store = MemoryStore(":memory:", dimension=64)
    mem = Memory(store, mock_embedder(dimension=64), chunk_size=1000, overlap=0)
    return EpisodicMemory(mem)


async def _gen(evs):
    for e in evs:
        yield e


def test_episode_to_text():
    assert "成功" in Episode("t", "o", True).to_text()
    assert "失败" in Episode("t", "o", False).to_text()


async def test_record_and_recall(mock_embedder):
    ep = _episodic(mock_embedder)
    await ep.record("给数组排序", "用了快速排序，通过", True)
    hits = await ep.recall("排序", 3)
    assert any("快速排序" in h.text for h in hits)


async def test_recorder_records_success_on_finish(mock_embedder):
    ep = _episodic(mock_embedder)
    rec = EpisodeRecorder(ep)
    evs = [TextDelta(text="hi"),
           RunFinished(message=Message(role=Role.ASSISTANT, content="搞定了"))]
    out = [e async for e in rec.wrap(_gen(evs), task="做个任务")]
    assert len(out) == 2                              # 透传全部事件
    hits = await ep.recall("任务", 3)
    assert any("成功" in h.text for h in hits)


async def test_recorder_records_failure_on_error(mock_embedder):
    ep = _episodic(mock_embedder)
    rec = EpisodeRecorder(ep)
    _ = [e async for e in rec.wrap(_gen([RunError(error="超时了")]), task="失败任务")]
    hits = await ep.recall("失败任务", 3)
    assert any("失败" in h.text for h in hits)


async def test_recorder_no_terminal_no_record(mock_embedder):
    ep = _episodic(mock_embedder)
    rec = EpisodeRecorder(ep)
    _ = [e async for e in rec.wrap(_gen([TextDelta(text="only")]), task="半截任务")]
    assert await ep.recall("半截任务", 3) == []      # 无终止事件 → 不记录


async def test_episodes_isolated_from_knowledge(mock_embedder):
    store = MemoryStore(":memory:", dimension=64)
    mem = Memory(store, mock_embedder(dimension=64), chunk_size=1000, overlap=0)
    ep = EpisodicMemory(mem)
    await mem.add_texts(["知识库内容"], "knowledge")
    await ep.record("任务A", "结果A", True)
    kn = await mem.search("内容", "knowledge", 5)
    assert all(h.collection == "knowledge" for h in kn)
    eh = await ep.recall("任务", 5)
    assert all(h.collection == "episodes" for h in eh)
```

运行：预期 FAIL。

- [ ] **步骤 2：实现 `src/harness/memory/episodic.py`**

```python
# src/harness/memory/episodic.py
from __future__ import annotations

from dataclasses import dataclass

from ..events import RunError, RunFinished
from .memory import Memory


@dataclass
class Episode:
    task: str
    outcome: str
    success: bool

    def to_text(self) -> str:
        status = "成功" if self.success else "失败"
        return f"任务：{self.task}\n结果（{status}）：{self.outcome}"


class EpisodicMemory:
    """绑定 episodes collection 的 Memory 薄门面。复用 ③a 全部向量设施。"""

    def __init__(self, memory: Memory, collection: str = "episodes") -> None:
        self._memory = memory
        self._collection = collection

    async def record(self, task: str, outcome: str, success: bool) -> list[int]:
        ep = Episode(task, outcome, success)
        return await self._memory.add_texts(
            [ep.to_text()], self._collection, {"success": success, "task": task[:200]})

    async def recall(self, query: str, k: int):
        return await self._memory.search(query, self._collection, k)


class EpisodeRecorder:
    """事件流包装器：透传事件，run 终止时自动记一条 episode。loop 零改动。"""

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
        if terminal:   # 只记录跑完（有终止事件）的 run
            await self._episodic.record(task, outcome, success)
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/test_episodic.py -v`　预期：6 passed。
```bash
git add src/harness/memory/episodic.py tests/test_episodic.py
git commit -m "feat: 情景记忆 Episode/EpisodicMemory/EpisodeRecorder"
```

---

## 任务 2：工具 recall_episodes / record_episode

**文件：** 创建 `src/harness/tools/builtins/episode_tools.py`、测试 `tests/test_episode_tools.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/test_episode_tools.py
from harness.memory.episodic import EpisodicMemory
from harness.memory.memory import Memory
from harness.memory.store import MemoryStore
from harness.tools.base import ToolRegistry, ToolExecutor
from harness.tools.builtins.episode_tools import RecallEpisodesTool, RecordEpisodeTool
from harness.types import ToolCall


def _ep(mock_embedder):
    store = MemoryStore(":memory:", dimension=64)
    mem = Memory(store, mock_embedder(dimension=64), chunk_size=1000, overlap=0)
    return EpisodicMemory(mem)


async def test_record_then_recall_tools(mock_embedder):
    ep = _ep(mock_embedder)
    reg = ToolRegistry()
    reg.register(RecordEpisodeTool(ep))
    reg.register(RecallEpisodesTool(ep, default_k=3))
    ex = ToolExecutor(reg)

    w = await ex.execute(ToolCall(id="c1", name="record_episode",
                                  arguments={"task": "排序数组", "lesson": "用快排最稳", "success": True}))
    assert w.is_error is False and "已记录" in w.content

    r = await ex.execute(ToolCall(id="c2", name="recall_episodes",
                                  arguments={"query": "排序"}))
    assert r.is_error is False and "快排" in r.content


async def test_recall_empty_message(mock_embedder):
    ep = _ep(mock_embedder)
    reg = ToolRegistry(); reg.register(RecallEpisodesTool(ep))
    ex = ToolExecutor(reg)
    r = await ex.execute(ToolCall(id="c1", name="recall_episodes", arguments={"query": "任何"}))
    assert "无相关历史经验" in r.content
```

运行：预期 FAIL。

- [ ] **步骤 2：实现 `src/harness/tools/builtins/episode_tools.py`**

```python
# src/harness/tools/builtins/episode_tools.py
from __future__ import annotations

from pydantic import BaseModel

from ..base import Tool
from ...memory.episodic import EpisodicMemory


class RecallEpisodesTool(Tool):
    name = "recall_episodes"
    description = "检索过往相似任务的经验（做法与成败），参考它来完成当前任务。"

    class Params(BaseModel):
        query: str
        k: int | None = None

    def __init__(self, episodic: EpisodicMemory, default_k: int = 3) -> None:
        self._episodic = episodic
        self._default_k = default_k

    async def run(self, params: "RecallEpisodesTool.Params") -> str:
        k = params.k if params.k is not None else self._default_k
        hits = await self._episodic.recall(params.query, k)
        if not hits:
            return "（无相关历史经验）"
        return "\n\n".join(f"[{i}] {h.text}" for i, h in enumerate(hits, 1))


class RecordEpisodeTool(Tool):
    name = "record_episode"
    description = "把一次任务的经验（做法/教训与成败）记录下来供以后参考。"

    class Params(BaseModel):
        task: str
        lesson: str
        success: bool = True

    def __init__(self, episodic: EpisodicMemory) -> None:
        self._episodic = episodic

    async def run(self, params: "RecordEpisodeTool.Params") -> str:
        await self._episodic.record(params.task, params.lesson, params.success)
        return "已记录经验。"
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/test_episode_tools.py -v`　预期：2 passed。
```bash
git add src/harness/tools/builtins/episode_tools.py tests/test_episode_tools.py
git commit -m "feat: recall_episodes / record_episode 情景记忆工具"
```

---

## 任务 3：端到端集成 + demo

**文件：** 测试 `tests/test_episodic_integration.py`、新增 `examples/episodic_demo.py`

- [ ] **步骤 1：写集成测试**（run1 被 recorder 包裹自动入库 → run2 用 recall_episodes 取到）

```python
# tests/test_episodic_integration.py
from harness.loop.agent_loop import AgentLoop
from harness.context.manager import ContextManager
from harness.tools.base import ToolRegistry
from harness.memory.episodic import EpisodicMemory, EpisodeRecorder
from harness.memory.memory import Memory
from harness.memory.store import MemoryStore
from harness.tools.builtins.episode_tools import RecallEpisodesTool
from harness.llm.base import StreamChunk, ToolCallDelta
from harness.events import ToolFinished


async def test_recorded_run_recalled_next(make_mock, text_turn, mock_embedder):
    store = MemoryStore(":memory:", dimension=64)
    mem = Memory(store, mock_embedder(dimension=64), chunk_size=1000, overlap=0)
    ep = EpisodicMemory(mem)
    rec = EpisodeRecorder(ep)

    # run1：无工具、直接作答；recorder 包裹 → 跑完自动记 episode
    loop1 = AgentLoop(client=make_mock([text_turn("用二分查找解决了")]),
                      registry=ToolRegistry(), context=ContextManager("s"),
                      max_steps=5, run_id_factory=lambda: "r1")
    _ = [e async for e in rec.wrap(loop1.run("在有序数组里查找"), task="在有序数组里查找")]

    # run2：recall_episodes 工具取到 run1 的经验
    reg = ToolRegistry(); reg.register(RecallEpisodesTool(ep, default_k=3))
    recall_turn = [
        StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
            index=0, id="c1", name="recall_episodes", arguments='{"query": "查找"}')),
        StreamChunk(type="done"),
    ]
    loop2 = AgentLoop(client=make_mock([recall_turn, text_turn("参考历史，用二分")]),
                      registry=reg, context=ContextManager("s"),
                      max_steps=5, run_id_factory=lambda: "r2")
    events = [e async for e in loop2.run("怎么在有序数组查找？")]

    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert "二分查找" in finished[0].result.content    # 召回了 run1 的经验
```

运行：`uv run pytest tests/test_episodic_integration.py -v`　预期：1 passed。

- [ ] **步骤 2：新增 `examples/episodic_demo.py`**

```python
# examples/episodic_demo.py
"""情景记忆手动验收：跑一个任务自动沉淀经验，再跑相似任务时 agent 用 recall_episodes 参考。
需要 .env 配好聊天端点与 embedding 端点。

运行：uv run python examples/episodic_demo.py
"""
from __future__ import annotations

import asyncio

from harness.config import HarnessConfig
from harness.context.manager import ContextManager
from harness.llm.openai_compat import OpenAICompatibleClient
from harness.loop.agent_loop import AgentLoop
from harness.memory.embeddings import OpenAICompatibleEmbeddingClient
from harness.memory.memory import Memory
from harness.memory.store import MemoryStore
from harness.memory.episodic import EpisodicMemory, EpisodeRecorder
from harness.tools.base import ToolRegistry
from harness.tools.builtins.episode_tools import RecallEpisodesTool
from harness.events import TextDelta, ToolStarted, ToolFinished, RunFinished


async def main() -> None:
    cfg = HarnessConfig()
    client = OpenAICompatibleClient(cfg)
    embedder = OpenAICompatibleEmbeddingClient(
        base_url=cfg.embedding_base_url, api_key=cfg.embedding_api_key or cfg.api_key,
        model=cfg.embedding_model, dimension=cfg.embedding_dimension)
    mem = Memory(MemoryStore(cfg.memory_db_path, cfg.embedding_dimension), embedder,
                 cfg.chunk_size, cfg.chunk_overlap)
    ep = EpisodicMemory(mem, cfg.episode_collection)
    rec = EpisodeRecorder(ep)

    # 第一次任务：recorder 包裹，跑完自动沉淀经验
    task1 = "用 Python 判断一个数是不是质数"
    loop1 = AgentLoop(client=client, registry=ToolRegistry(),
                      context=ContextManager("你是编程助手，简洁作答。"),
                      max_steps=cfg.max_steps, model_name=cfg.model)
    print(f"=== 任务1：{task1} ===")
    async for ev in rec.wrap(loop1.run(task1), task=task1):
        if isinstance(ev, TextDelta):
            print(ev.text, end="", flush=True)
    print("\n（已自动沉淀为情景记忆）\n")

    # 第二次相似任务：agent 可用 recall_episodes 参考
    reg = ToolRegistry(); reg.register(RecallEpisodesTool(ep, cfg.episode_recall_k))
    loop2 = AgentLoop(client=client, registry=reg,
                      context=ContextManager("你是编程助手。开始前可用 recall_episodes 查过往相似经验参考。"),
                      max_steps=cfg.max_steps, model_name=cfg.model)
    print("=== 任务2：判断质数（相似）===")
    async for ev in loop2.run("再写一次判断质数的函数，并说明思路"):
        if isinstance(ev, TextDelta):
            print(ev.text, end="", flush=True)
        elif isinstance(ev, ToolStarted):
            print(f"\n[检索经验] {ev.tool_call.arguments}")
        elif isinstance(ev, ToolFinished):
            print(f"[命中] {ev.result.content[:150]}")
        elif isinstance(ev, RunFinished):
            print(f"\n[完成]")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **步骤 3：全量测试并 commit**

运行：`uv run pytest -q`　预期：全绿（既有 3 个 skip 不变）。
```bash
git add tests/test_episodic_integration.py examples/episodic_demo.py
git commit -m "feat: 情景记忆端到端集成测试 + demo"
```

---

## 完成标准（对照规格验收）

- [ ] `record`→`recall` 往返召回（任务 1）
- [ ] `EpisodeRecorder.wrap` 对 RunFinished/RunError 自动落 success=True/False、无终止事件不记录（任务 1）
- [ ] `record_episode` 写入可被 `recall_episodes` 召回（任务 2）
- [ ] collection 隔离：episodes 不串 knowledge（任务 1）
- [ ] 集成：loop 被 recorder 包裹自动入库 → 另一 run 的 recall_episodes 取到（任务 3）
- [ ] ③a 及以往测试无回归、无新依赖（`uv run pytest` 全绿）
```

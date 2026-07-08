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

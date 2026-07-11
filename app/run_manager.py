# app/run_manager.py
"""进程内运行管理器：把一轮 AI 生成跑成脱离请求的后台任务，事件走内存 pub-sub 总线。

请求（原始 /api/chat 或刷新后的 attach）只是总线的订阅者：断开只取消订阅，后台任务照跑到
完并落库。订阅时先回放本 run 已缓冲的全部事件（补上错过的部分），再接实时事件直到结束——
这就是「刷新后无缝续上」的核心。

约束：内存态，单进程。多 worker 需粘性会话；服务重启会丢在途任务（由 store.reconcile_streaming
在启动时把残留 streaming 消息标 interrupted 兜底）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Awaitable, Callable

logger = logging.getLogger("app.runs")

_SENTINEL = object()   # 订阅队列的结束哨兵


class _RunHandle:
    def __init__(self, conv_id: str) -> None:
        self.conv_id = conv_id
        self.events: list = []                      # 本 run 至今的全部事件（供迟到订阅者回放）
        self.subscribers: set[asyncio.Queue] = set()
        self.done = False
        self.task: asyncio.Task | None = None


class RunManager:
    def __init__(self, grace_seconds: float = 60.0) -> None:
        self._runs: dict[str, _RunHandle] = {}
        self._lock = asyncio.Lock()
        self._grace = grace_seconds

    def is_active(self, run_id: str) -> bool:
        return run_id in self._runs

    def conv_of(self, run_id: str) -> str | None:
        h = self._runs.get(run_id)
        return h.conv_id if h else None

    def active_run_for_conv(self, conv_id: str) -> str | None:
        """该会话是否有未完成的在途 run（并发守卫用）。"""
        for rid, h in self._runs.items():
            if h.conv_id == conv_id and not h.done:
                return rid
        return None

    async def start(self, run_id: str, conv_id: str, agen: AsyncIterator) -> None:
        """起一个后台任务驱动 agen（产出 Event 的 async generator），逐事件缓冲+扇出。"""
        handle = _RunHandle(conv_id)
        self._runs[run_id] = handle
        handle.task = asyncio.create_task(self._drive(run_id, agen))

    async def _drive(self, run_id: str, agen: AsyncIterator) -> None:
        handle = self._runs[run_id]
        try:
            async for ev in agen:
                self._emit(run_id, ev)
        except asyncio.CancelledError:
            # stop 触发：agen 的 finally 已落 stopped 态；这里正常收尾即可
            pass
        except Exception as e:                       # noqa: BLE001
            logger.exception("run %s 生成失败", run_id)
            from harness.events import RunError
            self._emit(run_id, RunError(error=str(e)))
        finally:
            handle.done = True
            for q in list(handle.subscribers):
                q.put_nowait(_SENTINEL)              # 通知订阅者结束
            asyncio.create_task(self._cleanup_later(run_id))

    def _emit(self, run_id: str, ev) -> None:
        handle = self._runs.get(run_id)
        if handle is None:
            return
        handle.events.append(ev)
        for q in list(handle.subscribers):
            q.put_nowait(ev)

    async def _cleanup_later(self, run_id: str) -> None:
        await asyncio.sleep(self._grace)             # 宽限期：供完成瞬间刷新的迟到 attach 回放
        self._runs.pop(run_id, None)

    async def subscribe(self, run_id: str) -> AsyncIterator:
        """回放本 run 已缓冲事件 + 实时后续，直到结束。run 不存在则空。"""
        handle = self._runs.get(run_id)
        if handle is None:
            return
        q: asyncio.Queue = asyncio.Queue()
        # 快照缓冲 + 注册队列必须原子（其间不 await），避免二者之间漏/重事件
        async with self._lock:
            buffered = list(handle.events)
            register = not handle.done
            if register:
                handle.subscribers.add(q)
        try:
            for ev in buffered:
                yield ev
            if not register:                          # 订阅时已结束：回放完即止
                return
            while True:
                ev = await q.get()
                if ev is _SENTINEL:
                    break
                yield ev
        finally:
            handle.subscribers.discard(q)

    def cancel(self, run_id: str) -> bool:
        """停止一个在途 run（取消后台任务 → 触发 agen finally 落 stopped + 已生成部分）。"""
        h = self._runs.get(run_id)
        if h and h.task and not h.task.done():
            h.task.cancel()
            return True
        return False

    async def close(self) -> None:
        """关停：取消全部在途任务（app shutdown 用）。"""
        for h in list(self._runs.values()):
            if h.task and not h.task.done():
                h.task.cancel()

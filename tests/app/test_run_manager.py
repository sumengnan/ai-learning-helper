import asyncio

import pytest

from app.run_manager import RunManager


async def _collect(rm, run_id, into):
    async for ev in rm.subscribe(run_id):
        into.append(ev)


async def test_subscribe_from_start_gets_all_and_ends():
    rm = RunManager(grace_seconds=0.1)
    feed: asyncio.Queue = asyncio.Queue()

    async def agen():
        while True:
            ev = await feed.get()
            if ev is None:
                return
            yield ev

    await rm.start("r1", "c1", agen())
    got: list = []
    task = asyncio.create_task(_collect(rm, "r1", got))
    await asyncio.sleep(0.02)
    for e in ["e1", "e2", "e3"]:
        feed.put_nowait(e)
    await asyncio.sleep(0.05)
    feed.put_nowait(None)              # 结束
    await asyncio.sleep(0.05)
    await task
    assert got == ["e1", "e2", "e3"]


async def test_late_subscribe_replays_buffer_then_live():
    rm = RunManager(grace_seconds=0.1)
    feed: asyncio.Queue = asyncio.Queue()

    async def agen():
        while True:
            ev = await feed.get()
            if ev is None:
                return
            yield ev

    await rm.start("r1", "c1", agen())
    feed.put_nowait("e1")
    feed.put_nowait("e2")
    await asyncio.sleep(0.05)          # 让后台先产出 e1/e2（未订阅）
    got: list = []
    task = asyncio.create_task(_collect(rm, "r1", got))
    await asyncio.sleep(0.03)
    assert got == ["e1", "e2"]         # 迟到订阅：先回放缓冲
    feed.put_nowait("e3")
    await asyncio.sleep(0.03)
    assert got == ["e1", "e2", "e3"]   # 再接实时
    feed.put_nowait(None)
    await asyncio.sleep(0.05)
    await task


async def test_fanout_two_subscribers():
    rm = RunManager(grace_seconds=0.1)
    feed: asyncio.Queue = asyncio.Queue()

    async def agen():
        while True:
            ev = await feed.get()
            if ev is None:
                return
            yield ev

    await rm.start("r1", "c1", agen())
    a: list = []
    b: list = []
    ta = asyncio.create_task(_collect(rm, "r1", a))
    tb = asyncio.create_task(_collect(rm, "r1", b))
    await asyncio.sleep(0.02)
    feed.put_nowait("x")
    feed.put_nowait(None)
    await asyncio.sleep(0.05)
    await ta
    await tb
    assert a == ["x"] and b == ["x"]   # 两个订阅者都拿到


async def test_late_subscribe_after_done_replays_and_ends():
    rm = RunManager(grace_seconds=5)     # 宽限期内 handle 仍在

    async def agen():
        yield "only"

    await rm.start("r1", "c1", agen())
    await asyncio.sleep(0.05)            # 跑完（已 done）
    got: list = []
    await _collect(rm, "r1", got)        # 完成瞬间的迟到 attach
    assert got == ["only"]               # 回放缓冲 + 立即结束


async def test_cancel_stops_and_runs_finally():
    rm = RunManager(grace_seconds=0.1)
    marks: list = []

    async def agen():
        try:
            yield "e1"
            await asyncio.sleep(10)      # 挂起
        except asyncio.CancelledError:
            marks.append("cancelled")
            raise

    await rm.start("r1", "c1", agen())
    await asyncio.sleep(0.03)
    assert rm.active_run_for_conv("c1") == "r1"
    assert rm.cancel("r1") is True
    await asyncio.sleep(0.05)
    assert "cancelled" in marks         # agen 的 finally 被触发


async def test_active_and_conv_helpers():
    rm = RunManager(grace_seconds=5)

    async def agen():
        yield "a"

    assert rm.is_active("nope") is False
    await rm.start("r1", "cX", agen())
    assert rm.is_active("r1") and rm.conv_of("r1") == "cX"
    await asyncio.sleep(0.05)
    assert rm.active_run_for_conv("cX") is None   # 已 done，不再算在途

import asyncio

from app.api.chat import _HEARTBEAT, _with_heartbeat


async def _agen(feed: asyncio.Queue):
    """把队列里的项逐个 yield，遇 None 结束（模拟 run_manager.subscribe）。"""
    while True:
        ev = await feed.get()
        if ev is None:
            return
        yield ev


async def _drain(src, into):
    async for item in src:
        into.append(item)


async def test_events_pass_through_without_heartbeat_when_flowing():
    # 事件比心跳间隔来得快 → 全部原样透传，不插心跳
    feed: asyncio.Queue = asyncio.Queue()
    for e in ["a", "b", "c"]:
        feed.put_nowait(e)
    feed.put_nowait(None)
    got: list = []
    await _drain(_with_heartbeat(_agen(feed), interval=5.0), got)
    assert got == ["a", "b", "c"]


async def test_idle_gap_emits_heartbeat_then_keeps_next_event():
    # 空闲超过间隔 → 发一个心跳；随后到来的事件不丢
    feed: asyncio.Queue = asyncio.Queue()
    got: list = []
    task = asyncio.create_task(_drain(_with_heartbeat(_agen(feed), interval=0.02), got))
    await asyncio.sleep(0.05)          # 静默 → 至少一个心跳
    assert got and all(x is _HEARTBEAT for x in got)
    n_beats = len(got)
    feed.put_nowait("real")            # 未决的 __anext__ 必须接住它
    await asyncio.sleep(0.03)
    # 心跳之后第一个非心跳项就是 real（事件没被吞）；其后可能又夹杂更多心跳
    assert got[n_beats] == "real"
    assert "real" not in got[:n_beats]
    feed.put_nowait(None)
    await asyncio.sleep(0.03)
    await task


async def test_interval_zero_disables_heartbeat():
    # interval<=0 → 纯透传，静默也不发心跳
    feed: asyncio.Queue = asyncio.Queue()
    got: list = []
    task = asyncio.create_task(_drain(_with_heartbeat(_agen(feed), interval=0), got))
    await asyncio.sleep(0.05)
    assert got == []                   # 没事件、也没心跳
    feed.put_nowait("x")
    feed.put_nowait(None)
    await asyncio.sleep(0.03)
    await task
    assert got == ["x"]


async def test_ends_when_source_exhausts():
    feed: asyncio.Queue = asyncio.Queue()
    feed.put_nowait("only")
    feed.put_nowait(None)
    got: list = []
    await asyncio.wait_for(
        _drain(_with_heartbeat(_agen(feed), interval=5.0), got), timeout=1.0)
    assert got == ["only"]

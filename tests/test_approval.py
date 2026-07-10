import asyncio

import pytest

from harness import approval, progress
from harness.events import ApprovalRequired, ApprovalResolved


async def test_no_context_passes_through():
    # 无审批上下文（CLI/测试）→ 直接放行
    assert await approval.request_approval("run_shell", "rm -rf /", "危险") is True


async def test_approve_flow():
    got = []
    etoken = progress.set_emitter(got.append)
    ctoken = approval.set_context(run_id="r1", timeout=5)
    try:
        task = asyncio.create_task(
            approval.request_approval("run_shell", "rm -rf /", "递归删除"))
        await asyncio.sleep(0)  # 让 task 跑到 await，注册 pending 并 emit 事件
        req = next(e for e in got if isinstance(e, ApprovalRequired))
        assert req.run_id == "r1" and req.command == "rm -rf /"
        assert approval.resolve(req.approval_id, True) is True
        assert await task is True
    finally:
        approval.reset_context(ctoken)
        progress.reset_emitter(etoken)
    assert any(isinstance(e, ApprovalResolved) and e.approved for e in got)


async def test_deny_flow():
    got = []
    etoken = progress.set_emitter(got.append)
    ctoken = approval.set_context(run_id="r1", timeout=5)
    try:
        task = asyncio.create_task(
            approval.request_approval("run_shell", "rm -rf /", "递归删除"))
        await asyncio.sleep(0)
        req = next(e for e in got if isinstance(e, ApprovalRequired))
        assert approval.resolve(req.approval_id, False) is True
        assert await task is False
    finally:
        approval.reset_context(ctoken)
        progress.reset_emitter(etoken)


async def test_unknown_id_returns_false():
    assert approval.resolve("does-not-exist", True) is False


async def test_timeout_denies():
    etoken = progress.set_emitter(lambda e: None)
    ctoken = approval.set_context(run_id="r1", timeout=0.05)
    try:
        assert await approval.request_approval("run_shell", "rm -rf /", "危险") is False
    finally:
        approval.reset_context(ctoken)
        progress.reset_emitter(etoken)

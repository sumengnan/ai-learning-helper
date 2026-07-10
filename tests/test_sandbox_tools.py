import asyncio

import pytest
from harness import approval, progress
from harness.events import ApprovalRequired
from harness.sandbox.local import LocalSandbox
from harness.tools.base import ToolRegistry, ToolExecutor
from harness.tools.builtins.fs_tools import WriteFileTool, ReadFileTool, ListFilesTool
from harness.tools.builtins.shell_tool import RunShellTool
from harness.tools.builtins.code_tool import RunPythonTool
from harness.types import ToolCall


async def _executor(sb):
    reg = ToolRegistry()
    reg.register(WriteFileTool(sb))
    reg.register(ReadFileTool(sb, max_chars=8000))
    reg.register(ListFilesTool(sb))
    reg.register(RunShellTool(sb, timeout=5, max_chars=8000))
    reg.register(RunPythonTool(sb, timeout=5, max_chars=8000))
    return ToolExecutor(reg)


async def test_write_read_list_roundtrip():
    sb = LocalSandbox(); await sb.start()
    try:
        ex = await _executor(sb)
        w = await ex.execute(ToolCall(id="c1", name="write_file",
                                      arguments={"path": "note.txt", "content": "hi"}))
        assert w.is_error is False
        r = await ex.execute(ToolCall(id="c2", name="read_file", arguments={"path": "note.txt"}))
        assert r.content == "hi"
        ls = await ex.execute(ToolCall(id="c3", name="list_files", arguments={"path": "."}))
        assert "note.txt" in ls.content
    finally:
        await sb.close()


async def test_run_python():
    sb = LocalSandbox(); await sb.start()
    try:
        ex = await _executor(sb)
        r = await ex.execute(ToolCall(id="c1", name="run_python",
                                      arguments={"code": "print(1+1)"}))
        assert "2" in r.content and r.is_error is False
    finally:
        await sb.close()


async def test_run_shell():
    sb = LocalSandbox(); await sb.start()
    try:
        ex = await _executor(sb)
        r = await ex.execute(ToolCall(id="c1", name="run_shell",
                                      arguments={"command": "echo abc"}))
        assert "abc" in r.content
    finally:
        await sb.close()


async def test_run_shell_nonzero_exit_is_error():
    sb = LocalSandbox(); await sb.start()
    try:
        ex = await _executor(sb)
        r = await ex.execute(ToolCall(id="c1", name="run_shell",
                                      arguments={"command": "exit 7"}))
        assert r.is_error is True            # 非零退出 → 标记失败
        assert "exit_code=7" in r.content    # 输出仍保留（无“工具执行出错”前缀）
    finally:
        await sb.close()


async def test_run_python_nonzero_exit_is_error():
    sb = LocalSandbox(); await sb.start()
    try:
        ex = await _executor(sb)
        r = await ex.execute(ToolCall(id="c1", name="run_python",
                                      arguments={"code": "import sys; sys.exit(5)"}))
        assert r.is_error is True
        assert "exit_code=5" in r.content
    finally:
        await sb.close()


async def test_dangerous_shell_requires_approval_deny():
    # 有审批上下文时，危险命令被拒 → ToolError（is_error），且不执行
    sb = LocalSandbox(); await sb.start()
    etoken = progress.set_emitter(lambda e: None)
    ctoken = approval.set_context(run_id="r1", timeout=0.05)  # 无人应答 → 超时拒绝
    try:
        ex = await _executor(sb)
        r = await ex.execute(ToolCall(id="c1", name="run_shell",
                                      arguments={"command": "rm -rf /workspace"}))
        assert r.is_error is True and "拒绝" in r.content
    finally:
        approval.reset_context(ctoken)
        progress.reset_emitter(etoken)
        await sb.close()


async def test_dangerous_shell_approved_runs():
    sb = LocalSandbox(); await sb.start()
    got = []
    etoken = progress.set_emitter(got.append)
    ctoken = approval.set_context(run_id="r1", timeout=5)
    try:
        ex = await _executor(sb)
        task = asyncio.create_task(ex.execute(ToolCall(
            id="c1", name="run_shell",
            # rm -rf 命中黑名单，但目标不存在 + -f → 退出 0、实际无害
            arguments={"command": "rm -rf __no_such_dir__ && echo hi"})))
        await asyncio.sleep(0)
        req = next(e for e in got if isinstance(e, ApprovalRequired))
        assert approval.resolve(req.approval_id, True)
        r = await task
        assert r.is_error is False and "hi" in r.content
    finally:
        approval.reset_context(ctoken)
        progress.reset_emitter(etoken)
        await sb.close()


async def test_safe_shell_never_prompts():
    # 安全命令不应触发审批（即便设了极短超时也能正常返回）
    sb = LocalSandbox(); await sb.start()
    got = []
    etoken = progress.set_emitter(got.append)
    ctoken = approval.set_context(run_id="r1", timeout=0.01)
    try:
        ex = await _executor(sb)
        r = await ex.execute(ToolCall(id="c1", name="run_shell",
                                      arguments={"command": "echo safe"}))
        assert r.is_error is False and "safe" in r.content
        assert not any(isinstance(e, ApprovalRequired) for e in got)
    finally:
        approval.reset_context(ctoken)
        progress.reset_emitter(etoken)
        await sb.close()


async def test_path_escape_is_error():
    sb = LocalSandbox(); await sb.start()
    try:
        ex = await _executor(sb)
        r = await ex.execute(ToolCall(id="c1", name="read_file",
                                      arguments={"path": "../../etc/passwd"}))
        assert r.is_error is True   # SandboxError 经 ToolExecutor 兜成 is_error
    finally:
        await sb.close()

import pytest
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


async def test_path_escape_is_error():
    sb = LocalSandbox(); await sb.start()
    try:
        ex = await _executor(sb)
        r = await ex.execute(ToolCall(id="c1", name="read_file",
                                      arguments={"path": "../../etc/passwd"}))
        assert r.is_error is True   # SandboxError 经 ToolExecutor 兜成 is_error
    finally:
        await sb.close()

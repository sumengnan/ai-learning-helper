"""示范用内置 MCP server：暴露若干**无状态、无 per-user 依赖**的工具。

这不是迁移现有工具，而是新写的示范——用来端到端证明 MCP 客户端链路可用。
有状态/per-user 工具（考试、附件、沙箱）不适合跨进程迁移，仍走请求期本地 Tool。

启动：
    python -m app.mcp_servers.example_server            # 默认 stdio
    python -m app.mcp_servers.example_server --http     # streamable-http（默认 127.0.0.1:9100）
"""
from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("example")


@mcp.tool()
def calc(expression: str) -> str:
    """计算一个算术表达式，支持 + - * / ** % 和括号。"""
    # 复用现成的受限 AST 求值（绝不 eval，天然安全）
    from harness.tools.builtins.calculator import safe_eval
    return str(safe_eval(expression))


@mcp.tool()
def now() -> str:
    """返回当前 UTC 时间的 ISO8601 字符串。"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", action="store_true", help="用 streamable-http 传输启动")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9100)
    args = parser.parse_args()
    if args.http:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()   # 默认 stdio


if __name__ == "__main__":
    main()

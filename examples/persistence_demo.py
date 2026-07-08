# examples/persistence_demo.py
"""持久化手动验收：一次 run 的轨迹落 SQLite，并演示 checkpoint。
需要 .env 配好聊天端点。

运行：uv run python examples/persistence_demo.py "帮我算 (12+8)*3"
"""
from __future__ import annotations

import asyncio
import sys

from harness.config import HarnessConfig
from harness.context.manager import ContextManager
from harness.llm.openai_compat import OpenAICompatibleClient
from harness.loop.agent_loop import AgentLoop
from harness.tools.base import ToolRegistry
from harness.tools.builtins.calculator import CalculatorTool
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink
from harness.persistence.checkpoint import CheckpointStore
from harness.events import TextDelta, RunFinished, RunError


async def main(msg: str) -> None:
    cfg = HarnessConfig()
    traj = TrajectoryStore(cfg.persistence_db_path)
    ckpt = CheckpointStore(cfg.persistence_db_path)
    sink = TrajectorySink(traj)

    reg = ToolRegistry(); reg.register(CalculatorTool())
    loop = AgentLoop(client=OpenAICompatibleClient(cfg), registry=reg,
                     context=ContextManager("你可以用 calculator 计算。"),
                     max_steps=cfg.max_steps, model_name=cfg.model, checkpoint_store=ckpt)

    run_id = None
    async for ev in sink.wrap(loop.run(msg)):
        if isinstance(ev, TextDelta):
            print(ev.text, end="", flush=True)
        elif isinstance(ev, RunFinished):
            print("\n[完成]")
        elif isinstance(ev, RunError):
            print(f"\n[出错] {ev.error}")

    # 展示落库的轨迹（取任一已存 run 的事件类型序列）
    rows = traj._conn.execute("SELECT DISTINCT run_id FROM trajectory_events").fetchall()
    if rows:
        rid = rows[-1][0]
        print(f"\n=== 轨迹已落库 run_id={rid} ===")
        for e in traj.load(rid):
            print(" ", e["type"])


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "帮我算 (12+8)*3"))

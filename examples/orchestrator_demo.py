# examples/orchestrator_demo.py
"""Plan-Execute-Reflect 编排器手动验收：一个多步任务走完整 规划→执行→反思→汇总。
需要 .env 配好聊天端点（HARNESS_API_KEY / HARNESS_BASE_URL / HARNESS_MODEL）。

运行：uv run python examples/orchestrator_demo.py "调研快排与归并排序的差异并给出选择建议"
"""
from __future__ import annotations

import asyncio
import sys

from app.assembly import build_harness
from app.config import AppConfig
from harness.events import Progress, RunError, RunFinished, TextDelta


async def main(msg: str) -> None:
    cfg = AppConfig(enable_orchestrator=True)
    if not cfg.api_key:
        print("需在 .env 配 HARNESS_API_KEY 才能跑真实端点 demo"); return
    h = build_harness(cfg)
    async for ev in h.orchestrator.run(msg):
        if isinstance(ev, Progress):
            if ev.scope == "plan":
                print(f"\n[计划] {ev.text}")
            elif ev.scope == "reflect":
                print(f"\n[反思] {ev.text}")
            else:
                print(f"\n[{ev.scope}] {ev.text}")
        elif isinstance(ev, TextDelta):
            print(ev.text, end="", flush=True)
        elif isinstance(ev, RunFinished):
            print(f"\n\n[完成] {ev.message.content}")
        elif isinstance(ev, RunError):
            print(f"\n\n[出错] {ev.error}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "调研快排与归并排序的差异并给出选择建议"))

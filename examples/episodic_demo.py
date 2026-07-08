# examples/episodic_demo.py
"""情景记忆手动验收：跑一个任务自动沉淀经验，再跑相似任务时 agent 用 recall_episodes 参考。
需要 .env 配好聊天端点与 embedding 端点。

运行：uv run python examples/episodic_demo.py
"""
from __future__ import annotations

import asyncio

from harness.config import HarnessConfig
from harness.context.manager import ContextManager
from harness.llm.openai_compat import OpenAICompatibleClient
from harness.loop.agent_loop import AgentLoop
from harness.memory.embeddings import OpenAICompatibleEmbeddingClient
from harness.memory.memory import Memory
from harness.memory.store import MemoryStore
from harness.memory.episodic import EpisodicMemory, EpisodeRecorder
from harness.tools.base import ToolRegistry
from harness.tools.builtins.episode_tools import RecallEpisodesTool
from harness.events import TextDelta, ToolStarted, ToolFinished, RunFinished


async def main() -> None:
    cfg = HarnessConfig()
    client = OpenAICompatibleClient(cfg)
    embedder = OpenAICompatibleEmbeddingClient(
        base_url=cfg.embedding_base_url, api_key=cfg.embedding_api_key or cfg.api_key,
        model=cfg.embedding_model, dimension=cfg.embedding_dimension)
    mem = Memory(MemoryStore(cfg.memory_db_path, cfg.embedding_dimension), embedder,
                 cfg.chunk_size, cfg.chunk_overlap)
    ep = EpisodicMemory(mem, cfg.episode_collection)
    rec = EpisodeRecorder(ep)

    # 第一次任务：recorder 包裹，跑完自动沉淀经验
    task1 = "用 Python 判断一个数是不是质数"
    loop1 = AgentLoop(client=client, registry=ToolRegistry(),
                      context=ContextManager("你是编程助手，简洁作答。"),
                      max_steps=cfg.max_steps, model_name=cfg.model)
    print(f"=== 任务1：{task1} ===")
    async for ev in rec.wrap(loop1.run(task1), task=task1):
        if isinstance(ev, TextDelta):
            print(ev.text, end="", flush=True)
    print("\n（已自动沉淀为情景记忆）\n")

    # 第二次相似任务：agent 可用 recall_episodes 参考
    reg = ToolRegistry(); reg.register(RecallEpisodesTool(ep, cfg.episode_recall_k))
    loop2 = AgentLoop(client=client, registry=reg,
                      context=ContextManager("你是编程助手。开始前可用 recall_episodes 查过往相似经验参考。"),
                      max_steps=cfg.max_steps, model_name=cfg.model)
    print("=== 任务2：判断质数（相似）===")
    async for ev in loop2.run("再写一次判断质数的函数，并说明思路"):
        if isinstance(ev, TextDelta):
            print(ev.text, end="", flush=True)
        elif isinstance(ev, ToolStarted):
            print(f"\n[检索经验] {ev.tool_call.arguments}")
        elif isinstance(ev, ToolFinished):
            print(f"[命中] {ev.result.content[:150]}")
        elif isinstance(ev, RunFinished):
            print(f"\n[完成]")


if __name__ == "__main__":
    asyncio.run(main())

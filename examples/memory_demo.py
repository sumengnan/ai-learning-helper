"""记忆/RAG 手动验收：先写入知识库，再让 agent 检索作答。
需要 .env 配好聊天端点与 embedding 端点（HARNESS_EMBEDDING_* / HARNESS_API_KEY）。

运行：uv run python examples/memory_demo.py
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
from harness.tools.base import ToolRegistry
from harness.tools.builtins.memory_search import SearchMemoryTool
from harness.tools.builtins.memory_write import RememberTool
from harness.events import TextDelta, ToolStarted, ToolFinished, RunFinished


async def main() -> None:
    cfg = HarnessConfig()
    embedder = OpenAICompatibleEmbeddingClient(
        base_url=cfg.embedding_base_url,
        api_key=cfg.embedding_api_key or cfg.api_key,
        model=cfg.embedding_model,
        dimension=cfg.embedding_dimension,
    )
    store = MemoryStore(cfg.memory_db_path, cfg.embedding_dimension)
    memory = Memory(store, embedder, cfg.chunk_size, cfg.chunk_overlap)

    await memory.add_texts(
        ["光合作用是植物利用光能把二氧化碳和水转化为葡萄糖和氧气的过程。"],
        cfg.memory_collection, {"source": "生物笔记"})

    registry = ToolRegistry()
    registry.register(SearchMemoryTool(memory, cfg.memory_collection))
    registry.register(RememberTool(memory, cfg.memory_collection))

    loop = AgentLoop(
        client=OpenAICompatibleClient(cfg),
        registry=registry,
        context=ContextManager(system_prompt="你可以用 search_memory 查询知识库来回答问题。"),
        max_steps=cfg.max_steps,
        model_name=cfg.model,
    )

    async for ev in loop.run("根据知识库，什么是光合作用？"):
        if isinstance(ev, TextDelta):
            print(ev.text, end="", flush=True)
        elif isinstance(ev, ToolStarted):
            print(f"\n[检索] {ev.tool_call.arguments}")
        elif isinstance(ev, ToolFinished):
            print(f"[命中] {ev.result.content[:120]}")
        elif isinstance(ev, RunFinished):
            print(f"\n\n[完成] {ev.message.content}")


if __name__ == "__main__":
    asyncio.run(main())

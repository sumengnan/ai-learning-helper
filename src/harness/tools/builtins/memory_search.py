from __future__ import annotations

from pydantic import BaseModel

from ..base import Tool
from ...memory.memory import Memory


class SearchMemoryTool(Tool):
    name = "search_memory"
    description = "在长期记忆/知识库中检索与查询相关的内容，返回最相关的若干条文本。"

    class Params(BaseModel):
        query: str
        k: int = 5

    def __init__(self, memory: Memory, collection: str = "knowledge") -> None:
        self._memory = memory
        self._collection = collection

    async def run(self, params: "SearchMemoryTool.Params") -> str:
        hits = await self._memory.search(params.query, self._collection, params.k)
        if not hits:
            return "（未在知识库中检索到相关内容）"
        lines = []
        for i, h in enumerate(hits, 1):
            src = h.metadata.get("source")
            tag = f"（来源：{src}）" if src else ""
            lines.append(f"[{i}]{tag} {h.text}")
        return "\n".join(lines)

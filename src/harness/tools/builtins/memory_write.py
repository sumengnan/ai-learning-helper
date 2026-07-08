from __future__ import annotations

from pydantic import BaseModel

from ..base import Tool
from ...memory.memory import Memory


class RememberTool(Tool):
    name = "remember"
    description = "把一段值得长期记住的信息写入知识库，供以后检索。"

    class Params(BaseModel):
        text: str

    def __init__(self, memory: Memory, collection: str = "knowledge") -> None:
        self._memory = memory
        self._collection = collection

    async def run(self, params: "RememberTool.Params") -> str:
        ids = await self._memory.add_texts([params.text], self._collection)
        return f"已记住（{len(ids)} 块）。"

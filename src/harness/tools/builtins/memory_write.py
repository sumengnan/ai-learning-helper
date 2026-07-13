from __future__ import annotations

from pydantic import BaseModel

from ..base import Tool
from ...memory.memory import Memory


class RememberTool(Tool):
    name = "remember"
    description = (
        "把一段值得长期记住的信息写入你的长期记忆，供以后自动检索参考。"
        "注意：这是你私有的记忆，不是用户的「知识库」；"
        "若用户要求把资料/数据保存到知识库，请改用 save_to_knowledge。")

    class Params(BaseModel):
        text: str

    def __init__(self, memory: Memory, collection: str = "knowledge") -> None:
        self._memory = memory
        self._collection = collection

    async def run(self, params: "RememberTool.Params") -> str:
        ids = await self._memory.add_texts([params.text], self._collection)
        return f"已记住（{len(ids)} 块）。"

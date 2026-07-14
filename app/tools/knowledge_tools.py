# app/tools/knowledge_tools.py
from __future__ import annotations

from pydantic import BaseModel

from harness.tools.base import Tool

from ..knowledge import EmptyDocument, strip_markdown


class SaveToKnowledgeTool(Tool):
    name = "save_to_knowledge"
    description = (
        "把一段整理好的内容永久保存到用户的「知识库」，之后可在知识库菜单查看、"
        "语义检索与删除。适用于用户明确要求把资料/网页/抓取到的数据存进知识库的场景。"
        "title 为条目标题，text 为正文。"
        "（若只是想让自己以后记住某事、而非用户的知识库，请改用 remember。）")

    class Params(BaseModel):
        title: str
        text: str

    def __init__(self, knowledge, user_id: str) -> None:
        self._knowledge = knowledge
        self._uid = user_id

    async def run(self, params: "SaveToKnowledgeTool.Params") -> str:
        # 只存纯文字内容：去掉 markdown 排版标记（##、**、列表、表格等），减少检索噪声
        text = strip_markdown(params.text)
        try:
            res = await self._knowledge.ingest_text(self._uid, params.title, text)
        except EmptyDocument:
            return "保存失败：内容为空。"
        return f"已保存到知识库：《{res['filename']}》（{res['num_chunks']} 块），可在知识库菜单查看。"

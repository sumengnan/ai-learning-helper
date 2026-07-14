# app/tools/knowledge_tools.py
from __future__ import annotations

from pydantic import BaseModel

from harness.tools.base import Tool

from ..knowledge import EmptyDocument, strip_markdown


class SaveToKnowledgeTool(Tool):
    name = "save_to_knowledge"
    description = (
        "把内容作为「可检索的知识素材」存入用户知识库，供以后语义检索/RAG 使用"
        "（可在知识库菜单查看、删除）。仅用于用户明确要「存进知识库 / 收藏资料」的场景。"
        "注意：这不是生成给用户的成品文档——若用户要把内容「整理成学习笔记 / 总结 / 报告 / 文档」"
        "这类给用户查看或下载的成品，请改用 save_download，而不是存回知识库。"
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
        # 末尾带机读标记〔知识ID:...〕：交付门据此在校验不通过时清理该轮误入库的条目（前端剥离不展示）
        return (f"已保存到知识库：《{res['filename']}》（{res['num_chunks']} 块），"
                f"可在知识库菜单查看。〔知识ID:{res['id']}〕")

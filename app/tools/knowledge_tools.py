# app/tools/knowledge_tools.py
from __future__ import annotations

from pydantic import BaseModel

from harness.tools.base import Tool
from harness.types import ToolOutput

from ..knowledge import EmptyDocument, strip_markdown
from ..sources import strip_citations


# 「在描述保存动作」而非「内容本身」的措辞。模型看过前置步骤里本工具的成功提示
# （「已保存到知识库：《X》，用户可在知识库菜单查看」），到了真正的保存步就把那句话
# 复述进 text——存进去的是一段过程汇报，检索时毫无价值，还挤占结果。
_SAVE_REPORT_SIGNALS = (
    "已保存到知识库", "已存入知识库", "已整理并保存", "已根据提供的信息",
    "可在知识库菜单", "适合存入知识库", "供后续参考使用",
)
# 长度闸门：真笔记若恰好谈到「知识库怎么用」也会命中上面的词。故只在**短文本**上判——
# 一份含背景/要点/结论的笔记不可能只有两三百字。宁可放过长的，不可错杀真内容：
# 错杀的代价是用户的笔记直接丢失，比留一条噪声严重得多。
_SAVE_REPORT_MAX_LEN = 300


def _looks_like_save_report(text: str) -> bool:
    """text 是否是「关于保存的说明」而非笔记正文。"""
    t = (text or "").strip()
    if len(t) > _SAVE_REPORT_MAX_LEN:
        return False
    return any(s in t for s in _SAVE_REPORT_SIGNALS)


class SaveToKnowledgeTool(Tool):
    name = "save_to_knowledge"
    description = (
        "把内容作为「可检索的知识素材」存入用户知识库，供以后语义检索/RAG 使用"
        "（可在知识库菜单查看、删除）。仅用于用户明确要「存进知识库 / 收藏资料」的场景。"
        "注意：这不是生成给用户的成品文档——若用户要把内容「整理成学习笔记 / 总结 / 报告 / 文档」"
        "这类给用户查看或下载的成品，请改用 save_download，而不是存回知识库。"
        # 实测：多步任务里「整理成笔记」那一步也调了本工具（还调了两次），而后面本就排着
        # 专门的保存步——同一份资料因此在库里躺了好几份。内容加工不产生入库动作。
        "同一份内容只存一次：若前置步骤已经保存过，不要再存第二份；"
        "「整理 / 归纳 / 撰写」这类内容加工步骤不要调用本工具，把内容交给后续的保存步即可。"
        "title 为条目标题，text 为**笔记正文本身**——不是对它的描述，"
        "更不要把「已保存…可在知识库菜单查看」这类完成情况汇报当作正文传进来。"
        "（若只是想让自己以后记住某事、而非用户的知识库，请改用 remember。）")

    class Params(BaseModel):
        title: str
        text: str

    def __init__(self, knowledge, user_id: str) -> None:
        self._knowledge = knowledge
        self._uid = user_id

    async def run(self, params: "SaveToKnowledgeTool.Params") -> "str | ToolOutput":
        if _looks_like_save_report(params.text):
            return (
                "保存失败：你传的是一段「关于保存动作的说明」，不是笔记正文。"
                "实测收到过这样的 text：「已根据提供的信息整理并保存了一份结构化的AI资讯笔记……"
                "您可以在知识库菜单中查看该内容」——这段话存进去对日后检索毫无价值，"
                "还会挤占检索结果。\n"
                "text 必须是**笔记本身的完整正文**（背景、要点、结论等实质内容），"
                "不是对它的描述、不是完成情况汇报，也不要复述本工具的成功提示。"
                "请把真正的正文放进 text 重新调用。")
        # 只存纯文字内容：去掉 markdown 排版标记（##、**、列表、表格等）与正文里的
        # 来源角标 [1]（脱离对话后无指向、是检索噪声），减少检索噪声
        text = strip_citations(strip_markdown(params.text))
        try:
            res = await self._knowledge.ingest_text(self._uid, params.title, text)
        except EmptyDocument:
            return "保存失败：内容为空。"
        # 末尾带机读标记〔知识ID:...〕：交付门据此在校验不通过时清理该轮误入库的条目（前端剥离不展示）。
        # 走 marker 而非拼进 text——它不进模型上下文，模型看不见就不会把这串 id 抄进回复正文。
        return ToolOutput(
            text=(f"已保存到知识库：《{res['filename']}》（{res['num_chunks']} 块），"
                  f"用户可在知识库菜单查看。"),
            marker=f"〔知识ID:{res['id']}〕")

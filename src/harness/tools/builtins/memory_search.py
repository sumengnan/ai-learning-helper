from __future__ import annotations

from pydantic import BaseModel

from ..base import Tool
from ...memory.memory import Memory


class _CollectionSearchTool(Tool):
    """向量库按 collection 检索的公共实现。

    子类只定义「查哪个 scope、对模型怎么自称」：name / description / _default_collection
    / _empty。检索逻辑本身两者完全一致，差异全在语义，所以刻意不把 collection 做成模型
    可传的参数——scope 由构造方按用户注入，模型不该也不能跨用户检索。
    """

    _default_collection = "knowledge"
    _empty = "（未检索到相关内容）"

    class Params(BaseModel):
        query: str
        k: int | None = None

    def __init__(
        self, memory: Memory, collection: str | None = None, default_k: int = 5
    ) -> None:
        self._memory = memory
        self._collection = collection or self._default_collection
        self._default_k = default_k

    async def run(self, params: "_CollectionSearchTool.Params") -> str:
        k = params.k if params.k is not None else self._default_k
        hits = await self._memory.search(params.query, self._collection, k)
        if not hits:
            return self._empty
        lines = []
        for i, h in enumerate(hits, 1):
            src = h.metadata.get("source")
            tag = f"（来源：{src}）" if src else ""
            lines.append(f"[{i}]{tag} {h.text}")
        return "\n".join(lines)


class SearchKnowledgeTool(_CollectionSearchTool):
    """检索用户知识库（上传/保存的资料）。作答的可引用依据，交付门的 grounding 判据。"""

    name = "search_knowledge"
    description = (
        "在用户的知识库中检索相关资料并返回原文片段。知识库装的是用户上传或保存的文档，"
        "是回答学习问题时可引用的依据。"
        "注意：这里查不到你自己记下的偏好/结论，那些请用 search_memory。")
    _default_collection = "knowledge"
    # 空命中文案被 app/tools/validating.py 的 NO_HIT_MARK 与 verify.py 的 _NO_HIT 逐字匹配，改动需同步
    _empty = "（未在知识库中检索到相关内容）"


class SearchMemoryTool(_CollectionSearchTool):
    """检索 AI 自己的长期记忆（remember 写入）。与知识库不同，这不是可引用的资料来源。"""

    name = "search_memory"
    description = (
        "检索你自己记下的长期记忆——那些你用 remember 存过的、关于这位用户的偏好、"
        "习惯和过往结论，跨会话有效。"
        "注意：这里没有用户的资料文档，查资料请用 search_knowledge。")
    _default_collection = "memory"
    _empty = "（未检索到相关的长期记忆）"

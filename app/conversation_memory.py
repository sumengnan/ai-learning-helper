# app/conversation_memory.py
from __future__ import annotations

from harness.memory.store import MemoryHit

# 召回时的 over-fetch 系数：metadata.seq 过滤会淘汰一部分命中，多取一些留余量。
_OVERFETCH = 3


class ConversationMemoryService:
    """L3 对话语义检索：把历史轮次 embed 进 conversation:<conv_id> collection，
    按当前问题召回窗口外最相关的片段。复用 harness.memory.Memory，不改内核。

    已知局限：Memory.add_texts 会对 >chunk_size 的文本再切块（长轮被拆碎）；
    MemoryStore 检索不读 metadata，seq 过滤在应用层做。整轮较短时按整轮入库可规避。
    """

    def __init__(self, memory, collection_prefix: str = "conversation") -> None:
        self._memory = memory
        self._prefix = collection_prefix

    def _collection_for(self, conv_id: str) -> str:
        return f"{self._prefix}:{conv_id}"

    async def record_turn(self, conv_id: str, seq: int, text: str) -> list[str]:
        """把一轮对话文本写入向量库。seq 为该轮在历史中的消息前缀位置（供窗口外过滤）。

        调用方应在轮结束落库后异步触发（每条要一次远程 embedding，勿阻塞聊天路径）。
        """
        if not text or not text.strip():
            return []
        return await self._memory.add_texts(
            [text], self._collection_for(conv_id), {"seq": seq})

    async def retrieve(self, conv_id: str, query: str, k: int, *,
                       before_seq: int | None = None) -> list[MemoryHit]:
        """按 query 召回该会话的相关历史片段。before_seq 给定时，只返回窗口外（seq < before_seq）。"""
        if not query or not query.strip() or k <= 0:
            return []
        hits = await self._memory.search(query, self._collection_for(conv_id), k * _OVERFETCH)
        if before_seq is not None:
            hits = [h for h in hits if (h.metadata or {}).get("seq", -1) < before_seq]
        return hits[:k]

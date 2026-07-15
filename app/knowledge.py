# app/knowledge.py
from __future__ import annotations

import hashlib
import re
from uuid import uuid4

from harness.memory.chunker import kind_for_filename

from .documents import _category
from .parsing import parse_file


def _clip(text: str, n: int = 300) -> str:
    return " ".join(text.split())[:n]


def _content_hash(text: str) -> str:
    """正文的去重指纹。

    先把空白压平再算：同一份内容改了换行/缩进/行尾空格重新导出，切出来的块与向量实质相同，
    不该因为几个空格就被当成新文档再 embedding 一遍。除此之外不做任何规范化 —— 大小写、
    标点的差异是真实的内容差异，不能抹掉。
    """
    return hashlib.sha256(" ".join((text or "").split()).encode("utf-8")).hexdigest()


def _table_row(m: re.Match) -> str:
    return "  ".join(c.strip() for c in m.group(1).split("|") if c.strip())


def strip_markdown(md: str) -> str:
    """去掉 markdown 排版标记，只留可读文字内容（供聊天保存到知识库用）。

    标题/加粗/斜体/内联代码/链接/图片/列表/引用/分隔线/代码围栏的标记都去掉，
    保留其文字（代码块保留代码正文、表格转为空格分隔的文本）；不改动纯文字内容。
    """
    t = md or ""
    t = re.sub(r"^```[^\n]*$", "", t, flags=re.M)            # 代码围栏行（保留代码正文）
    t = re.sub(r"^\s{0,3}#{1,6}\s+", "", t, flags=re.M)       # 标题 #
    t = re.sub(r"^\s{0,3}>\s?", "", t, flags=re.M)            # 引用 >
    t = re.sub(r"^\s*([-*_])(?:\s*\1){2,}\s*$", "", t, flags=re.M)  # 分隔线 ---
    t = re.sub(r"^\s*[-*+]\s+", "", t, flags=re.M)            # 无序列表 -
    t = re.sub(r"^\s*\d+\.\s+", "", t, flags=re.M)            # 有序列表 1.
    t = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", t)           # 图片 → alt
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)            # 链接 → 文字
    t = re.sub(r"\*\*([^*]+)\*\*|__([^_]+)__", lambda m: m.group(1) or m.group(2), t)  # 加粗
    t = re.sub(r"\*([^*\n]+)\*", r"\1", t)                    # 斜体 *
    t = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", t)         # 斜体 _
    t = re.sub(r"`([^`]+)`", r"\1", t)                       # 内联代码
    # 表格：用 [ \t] 而非 \s，避免行尾空白匹配吞掉换行导致相邻行粘连
    t = re.sub(r"^[ \t]*\|[ \t:|-]+\|[ \t]*$", "", t, flags=re.M)     # 分隔行 |---|
    t = re.sub(r"^[ \t]*\|(.+)\|[ \t]*$", _table_row, t, flags=re.M)  # 表格行 → 空格分隔
    t = re.sub(r"\n{3,}", "\n\n", t)                          # 压缩多余空行
    return t.strip()


class EmptyDocument(Exception):
    pass


class KnowledgeService:
    def __init__(self, memory, memory_store, doc_store, collection: str = "knowledge") -> None:
        self._memory = memory
        self._memory_store = memory_store
        self._doc_store = doc_store
        self._collection = collection

    def _collection_for(self, user_id: str) -> str:
        return f"{self._collection}:{user_id}"

    async def ingest(self, user_id: str, filename: str, data: bytes) -> dict:
        text = parse_file(filename, data)          # UnsupportedFormat 冒泡
        if not text.strip():
            raise EmptyDocument(filename)
        return await self._ingest_text(user_id, filename, text, size=len(data))

    async def _ingest_text(self, user_id: str, source: str, text: str, *, size: int) -> dict:
        """两个导入入口的共同实现：查重 → 切块+embedding → 建文档记录。

        查重按**正文** hash，且必须赶在 add_texts 之前 —— 那一步要打 embedding 端点，
        重复内容走到那儿钱就已经花了，而且会把一模一样的向量再灌一遍进库、污染检索
        （同内容命中两次，白占候选池名额）。
        """
        content_hash = _content_hash(text)
        dup = self._doc_store.find_by_hash(user_id, content_hash)
        if dup is not None:
            # 命中已有同内容文档：不重复切块/embedding/入库，回指原文档。
            # duplicate 标记供接口层告知用户「这份已经在库里了」，而非假装导入成功。
            return {"id": dup["id"], "filename": dup["filename"],
                    "num_chunks": dup["num_chunks"], "duplicate": True}
        doc_id = uuid4().hex
        chunk_ids = await self._memory.add_texts(
            [text], self._collection_for(user_id),
            {"source": source, "doc_id": doc_id, "user_id": user_id},
            kind=kind_for_filename(source))
        excerpt = " ".join(text.split())[:200]     # 压平空白后取首段作摘要
        self._doc_store.create(user_id, doc_id, source, size, chunk_ids, excerpt,
                               content_hash=content_hash)
        return {"id": doc_id, "filename": source, "num_chunks": len(chunk_ids),
                "duplicate": False}

    async def ingest_text(self, user_id: str, title: str, text: str) -> dict:
        """把一段文本作为正式文档存入用户知识库（供聊天中「保存到知识库」使用）。

        与 ingest 一致：写入 knowledge:{user_id} 向量集合并建立 doc_store 文档记录，
        故会出现在知识库菜单、可检索、可删除、同样参与去重。区别仅是输入为文本而非上传文件。
        """
        if not text.strip():
            raise EmptyDocument(title)
        return await self._ingest_text(user_id, title, text,
                                       size=len(text.encode("utf-8")))

    def list_fragments(self, user_id: str, page: int = 1, size: int = 8,
                       category: str | None = None) -> dict:
        """分页列举该用户知识库的所有片段（chunk），每片一项。

        category 给定时按分类筛选。分类由文件名后缀运行时推导、非独立存储列，
        故走「取全量 → 过滤 → 应用层分页」（个人知识库规模可接受）。
        """
        kind = self._collection
        if not category:
            offset = (max(1, page) - 1) * size
            records = self._memory_store.list_by_owner(user_id, kind, limit=size, offset=offset)
            items = [self._fragment(r.id, r.text, r.metadata, r.created_at) for r in records]
            return {"items": items, "total": self._memory_store.count_by_owner(user_id, kind)}
        records = self._memory_store.list_by_owner(user_id, kind)      # 全量
        matched = [r for r in records
                   if _category((r.metadata or {}).get("source", "")) == category]
        offset = (max(1, page) - 1) * size
        page_recs = matched[offset:offset + size]
        items = [self._fragment(r.id, r.text, r.metadata, r.created_at) for r in page_recs]
        return {"items": items, "total": len(matched)}

    async def search(self, user_id: str, query: str, k: int = 30) -> list[dict]:
        """片段级语义检索：每个命中 chunk 一项，带相关度。"""
        hits = await self._memory.search(query, self._collection_for(user_id), k)
        results = []
        for h in hits:
            # distance = 1 - 融合分（越小越相关，可能为负）；相关度 = 融合分裁剪到 0–100%
            relevance = max(0, min(100, round((1 - h.distance) * 100)))
            item = self._fragment(h.id, h.text, h.metadata, h.created_at)
            item["relevance"] = relevance
            results.append(item)
        results.sort(key=lambda r: r["relevance"], reverse=True)
        return results

    def get_fragment(self, user_id: str, chunk_id: str) -> dict | None:
        """单个片段详情：完整正文 + 来源/分类/日期。找不到或非本人返回 None。"""
        recs = self._memory_store.get([chunk_id])
        if not recs or recs[0].owner_id != user_id:
            return None
        r = recs[0]
        md = r.metadata or {}
        filename = md.get("source", "")
        return {"id": r.id, "filename": filename, "text": r.text,
                "category": _category(filename), "uploaded_at": r.created_at,
                "doc_id": md.get("doc_id", "")}

    @staticmethod
    def _fragment(chunk_id: str, text: str, metadata: dict, created_at: str) -> dict:
        filename = (metadata or {}).get("source", "")
        return {"id": chunk_id, "filename": filename, "excerpt": _clip(text),
                "category": _category(filename), "uploaded_at": created_at}

    def delete_fragment(self, user_id: str, chunk_id: str) -> bool:
        """删除单个片段：从向量库移除并回写父文档 chunk_ids/num_chunks。找不到返回 False。"""
        recs = self._memory_store.get([chunk_id])
        if not recs or recs[0].owner_id != user_id:
            return False
        doc_id = (recs[0].metadata or {}).get("doc_id")
        self._memory_store.delete([chunk_id])
        if doc_id:
            self._doc_store.remove_chunk(user_id, doc_id, chunk_id)
        return True

    def delete(self, user_id: str, doc_id: str) -> None:
        self._memory_store.delete(self._doc_store.chunk_ids(user_id, doc_id))
        self._doc_store.delete(user_id, doc_id)

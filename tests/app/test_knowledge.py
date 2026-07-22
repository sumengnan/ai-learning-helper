# tests/app/test_knowledge.py
from app.knowledge import KnowledgeService, EmptyDocument
from app.documents import DocumentStore
from harness.memory.memory import Memory
from harness.memory.sqlite_backend import SqliteVecBackend
from harness.memory.retriever import Retriever, RetrievalConfig
from harness.memory.reranker import RERANK_SCORE_KEY
import pytest


def _service(mock_embedder):
    store = SqliteVecBackend(":memory:", dimension=64)
    mem = Memory(store, mock_embedder(dimension=64), chunk_size=1000, overlap=0)
    return KnowledgeService(mem, store, DocumentStore(":memory:")), mem


class _StubReranker:
    """把绝对精排分写进 candidate.components[RERANK_SCORE_KEY]，模拟真实 qwen3-rerank。

    scores: {正文子串: 分数}——命中子串就打对应绝对分（[0,1] 量纲）。
    """

    def __init__(self, scores: dict[str, float]):
        self._scores = scores

    async def rerank(self, query, candidates):
        for c in candidates:
            for key, s in self._scores.items():
                if key in c.record.text:
                    c.components[RERANK_SCORE_KEY] = s
        return candidates


def _service_with_rerank(mock_embedder, scores: dict[str, float]):
    """带精排的 KnowledgeService：注入按正文打绝对分的 stub reranker。"""
    store = SqliteVecBackend(":memory:", dimension=64)
    emb = mock_embedder(dimension=64)
    retriever = Retriever(store, emb, _StubReranker(scores), RetrievalConfig())
    mem = Memory(store, emb, chunk_size=1000, overlap=0, retriever=retriever)
    return KnowledgeService(mem, store, DocumentStore(":memory:")), mem


async def test_ingest_registers_and_searchable(mock_embedder):
    svc, mem = _service(mock_embedder)
    doc = await svc.ingest("u1", "bio.txt", "光合作用把二氧化碳和水转化为氧气".encode())
    assert doc["num_chunks"] >= 1 and doc["filename"] == "bio.txt"
    hits = await mem.search("光合作用", "knowledge:u1", 3)
    assert any("光合作用" in h.text for h in hits)


async def test_delete_removes_chunks_and_doc(mock_embedder):
    svc, mem = _service(mock_embedder)
    doc = await svc.ingest("u1", "a.txt", "独特内容ABC".encode())
    svc.delete("u1", doc["id"])
    assert await mem.search("独特内容", "knowledge:u1", 3) == []
    assert svc._doc_store.exists("u1", doc["id"]) is False


async def test_empty_document_raises(mock_embedder):
    svc, _ = _service(mock_embedder)
    with pytest.raises(EmptyDocument):
        await svc.ingest("u1", "empty.txt", "   ".encode())


async def test_ingest_text_shows_in_user_knowledge_list(mock_embedder):
    """聊天中保存的文本应作为正式文档进入该用户的知识库列表（owner=user_id）。"""
    svc, mem = _service(mock_embedder)
    doc = await svc.ingest_text("u1", "AI 最新数据", "2026 年模型参数规模持续增长")
    assert doc["num_chunks"] >= 1 and doc["filename"] == "AI 最新数据"
    # 出现在知识库菜单（list_fragments 按 owner=user_id 过滤）
    page = svc.list_fragments("u1", 1, 10)
    assert page["total"] >= 1
    assert any(item["filename"] == "AI 最新数据" for item in page["items"])
    # 建立了 doc_store 文档记录（可删除）
    assert svc._doc_store.exists("u1", doc["id"]) is True
    # 语义可检索
    hits = await mem.search("模型参数", "knowledge:u1", 3)
    assert any("参数" in h.text for h in hits)


async def test_ingest_text_empty_raises(mock_embedder):
    svc, _ = _service(mock_embedder)
    with pytest.raises(EmptyDocument):
        await svc.ingest_text("u1", "空", "   ")


async def test_list_fragments_filter_by_category(mock_embedder):
    """按分类（文件名后缀推导）筛选知识库片段。"""
    svc, _ = _service(mock_embedder)
    await svc.ingest_text("u1", "a.txt", "文本内容一")     # 文本
    await svc.ingest_text("u1", "b.docx", "word 内容")     # Word
    await svc.ingest_text("u1", "c.txt", "文本内容二")     # 文本
    await svc.ingest_text("u1", "d.md", "# 标题")          # Markdown

    text_page = svc.list_fragments("u1", 1, 10, category="文本")
    assert text_page["total"] == 2
    assert all(it["category"] == "文本" for it in text_page["items"])

    word_page = svc.list_fragments("u1", 1, 10, category="Word")
    assert word_page["total"] == 1

    # 不传分类返回全部
    assert svc.list_fragments("u1", 1, 10)["total"] == 4


async def test_list_fragments_category_paginates(mock_embedder):
    svc, _ = _service(mock_embedder)
    for i in range(5):
        await svc.ingest_text("u1", f"n{i}.txt", f"文本 {i}")
    p1 = svc.list_fragments("u1", 1, 2, category="文本")
    p3 = svc.list_fragments("u1", 3, 2, category="文本")
    assert p1["total"] == 5 and len(p1["items"]) == 2
    assert len(p3["items"]) == 1                # 第 3 页余 1 条


# ---------- 重复导入去重 ----------

class _CountingEmbedder:
    """记录 embed 调用次数的假 embedder —— 去重必须发生在 embedding 之前（那步要花钱）。"""

    def __init__(self, dimension: int = 64):
        self.dimension = dimension
        self.calls = 0

    async def embed(self, texts):
        self.calls += 1
        return [[0.1] * self.dimension for _ in texts]


def _counting_service():
    store = SqliteVecBackend(":memory:", dimension=64)
    emb = _CountingEmbedder(64)
    mem = Memory(store, emb, chunk_size=1000, overlap=0)
    return KnowledgeService(mem, store, DocumentStore(":memory:")), store, emb


async def test_reingest_same_content_does_not_duplicate_vectors():
    """同一内容导入两次 → 向量库里只有一份，不产生完全重复的块。"""
    svc, store, emb = _counting_service()
    data = "光合作用把二氧化碳和水转化为氧气".encode()
    first = await svc.ingest("u1", "bio.txt", data)
    n_after_first = store.count_by_owner("u1", "knowledge")

    second = await svc.ingest("u1", "bio.txt", data)
    assert second["duplicate"] is True
    assert second["id"] == first["id"], "应回指原文档，而不是新建一个"
    assert store.count_by_owner("u1", "knowledge") == n_after_first, "向量不该变多"


async def test_dedup_happens_before_embedding():
    """去重要赶在 embedding 之前 —— 那一步打远端、花钱，重复内容不该付这笔。"""
    svc, _store, emb = _counting_service()
    data = "一段内容".encode()
    await svc.ingest("u1", "a.txt", data)
    assert emb.calls == 1
    await svc.ingest("u1", "a.txt", data)
    assert emb.calls == 1, "重复导入不该再打一次 embedding"


async def test_dedup_ignores_whitespace_only_differences():
    """改了换行/缩进重新导出 → 切出的块与向量实质相同，仍算重复。"""
    svc, store, _emb = _counting_service()
    await svc.ingest("u1", "a.txt", "第一行\n第二行".encode())
    n = store.count_by_owner("u1", "knowledge")
    dup = await svc.ingest("u1", "a.txt", "第一行\n\n  第二行  \n".encode())
    assert dup["duplicate"] is True
    assert store.count_by_owner("u1", "knowledge") == n


async def test_dedup_is_per_user():
    """去重按用户 —— 别人库里有同样内容，不影响我导入自己的那份。"""
    svc, store, _emb = _counting_service()
    data = "公共知识".encode()
    await svc.ingest("u1", "a.txt", data)
    mine = await svc.ingest("u2", "a.txt", data)
    assert mine["duplicate"] is False
    assert store.count_by_owner("u2", "knowledge") >= 1


async def test_different_content_same_filename_is_not_duplicate():
    """同名但内容改了 → 是新版本，照常导入（去重看内容，不看文件名）。"""
    svc, _store, emb = _counting_service()
    await svc.ingest("u1", "note.txt", "第一版内容".encode())
    second = await svc.ingest("u1", "note.txt", "改过的第二版内容".encode())
    assert second["duplicate"] is False
    assert emb.calls == 2


async def test_same_content_different_format_is_deduped():
    """同一份内容存成 .txt 与 .md 各传一次：字节不同，但正文相同 → 仍是重复。

    这正是按「解析后正文」而非「原始字节」算 hash 的理由。
    """
    svc, store, _emb = _counting_service()
    text = "纯文字内容没有任何标记"
    await svc.ingest("u1", "a.txt", text.encode())
    n = store.count_by_owner("u1", "knowledge")
    dup = await svc.ingest("u1", "a.md", text.encode())
    assert dup["duplicate"] is True
    assert store.count_by_owner("u1", "knowledge") == n


async def test_ingest_text_also_deduped(mock_embedder):
    """聊天里的 save_to_knowledge 走同一条去重路径。"""
    svc, _mem = _service(mock_embedder)
    first = await svc.ingest_text("u1", "笔记", "一段要保存的内容")
    second = await svc.ingest_text("u1", "笔记", "一段要保存的内容")
    assert second["duplicate"] is True and second["id"] == first["id"]


async def test_first_ingest_reports_not_duplicate(mock_embedder):
    svc, _mem = _service(mock_embedder)
    doc = await svc.ingest("u1", "a.txt", "全新内容".encode())
    assert doc["duplicate"] is False


async def test_legacy_rows_without_hash_do_not_match_each_other():
    """旧库文档的 content_hash 为 NULL；空 hash 之间不该互相认成重复。"""
    svc, _store, _emb = _counting_service()
    ds = svc._doc_store
    ds.create("u1", "old1", "old.txt", 10, ["c1"], "摘要")          # 不传 hash（模拟旧行）
    ds.create("u1", "old2", "old2.txt", 10, ["c2"], "摘要")
    assert ds.find_by_hash("u1", "") is None
    # 新导入照常，不会被旧行干扰
    doc = await svc.ingest("u1", "new.txt", "新内容".encode())
    assert doc["duplicate"] is False


# ---------- 入库失败要给人话 + embedding 分批 ----------

def test_ingest_hint_maps_real_batch_error():
    """用户实测的 400：DashScope 单次最多 20 条，超了直接报 batch size invalid。"""
    from app.api.documents import _ingest_hint
    raw = Exception("Error code: 400 - {'error': {'message': '<400> InternalError.Algo."
                    "InvalidParameter: Value error, batch size is invalid, it should not be "
                    "larger than 20.: input.contents'}}")
    h = _ingest_hint(raw)
    assert "上传失败" in h and "拆成几个小文件" in h
    assert "InternalError" not in h and "400" not in h, "厂商原文不该露给用户"


def test_ingest_hint_covers_common_causes_and_falls_back():
    from app.api.documents import _ingest_hint
    assert "限流" in _ingest_hint(Exception("Rate limit exceeded"))
    assert "密钥" in _ingest_hint(Exception("Incorrect API key provided"))
    assert "额度" in _ingest_hint(Exception("insufficient quota"))
    # 认不出的走通用兜底，但仍要告诉用户下一步做什么
    g = _ingest_hint(Exception("某种没见过的故障"))
    assert "稍后重试" in g and "服务端日志" in g


async def test_embedding_client_splits_oversized_batches():
    """回归：整篇文档的全部分块一次性发出去，稍长的文档必然超限。

    切片后必须按原顺序拼回——分块与向量错位比直接失败更糟（检索会张冠李戴）。
    """
    from harness.memory.embeddings import OpenAICompatibleEmbeddingClient as C
    c = C.__new__(C)
    c.dimension, c._model, c._batch = 4, "m", 20
    sizes = []

    async def _fake(texts):
        sizes.append(len(texts))
        return [[float(t)] for t in texts]
    c._embed_batch = _fake
    out = await c.embed(list(range(45)))
    assert sizes == [20, 20, 5], "应按上限切片"
    assert out == [[float(i)] for i in range(45)], "必须按原顺序拼回"


# ---------- 相关度% = 精排绝对分（而非 minmax 相对排名分） ----------

async def test_search_relevance_uses_rerank_absolute_score(mock_embedder):
    """开精排时，相关度%取精排绝对分：0.26 → 26%，且标 relevance_kind='rerank'。

    变异钩子：若把显示改回 (1-distance)*100 的 minmax 逻辑，单条命中会被归一化成
    满分（distance<=0 → 裁到 100%），此断言（==26）必然失败。
    """
    svc, _mem = _service_with_rerank(mock_embedder, {"厨具": 0.26})
    await svc.ingest_text("u1", "无关.txt", "不锈钢厨具清洗保养指南")
    hits = await svc.search("u1", "厨具", k=5)
    assert len(hits) == 1
    assert hits[0]["relevance"] == 26          # 绝对分，不是被 minmax 抹成的 100
    assert hits[0]["relevance_kind"] == "rerank"


async def test_search_related_vs_unrelated_separated_by_rerank(mock_embedder):
    """相关与无关文档的相关度%应拉开：related≈43% 明显高于 unrelated≈26%。"""
    svc, _mem = _service_with_rerank(mock_embedder, {"相关正文": 0.43, "无关正文": 0.26})
    await svc.ingest_text("u1", "a.txt", "相关正文一段")
    await svc.ingest_text("u1", "b.txt", "无关正文一段")
    hits = await svc.search("u1", "查询", k=5)
    by_rel = {h["excerpt"][:4] if h.get("excerpt") else h["relevance"]: h for h in hits}
    rels = sorted(h["relevance"] for h in hits)
    assert rels == [26, 43]
    assert all(h["relevance_kind"] == "rerank" for h in hits)


async def test_search_falls_back_to_rank_score_without_rerank(mock_embedder):
    """精排关闭（无精排分）时回退到相对排名分，并标 relevance_kind='rank'，供前端提示。"""
    svc, _mem = _service(mock_embedder)          # 默认 NoOpReranker，无精排分
    await svc.ingest_text("u1", "a.txt", "一段可检索的内容")
    hits = await svc.search("u1", "内容", k=5)
    assert len(hits) >= 1
    assert all(h["relevance_kind"] == "rank" for h in hits)
    assert all(0 <= h["relevance"] <= 100 for h in hits)

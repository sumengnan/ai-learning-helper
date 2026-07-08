import json
import pytest
from harness.memory.memory import Memory
from harness.memory.store import MemoryStore
from app.questions import QuestionStore
from app.quiz_service import QuizService, QuizError, NoKnowledge


def _memory(mock_embedder):
    return Memory(MemoryStore(":memory:", dimension=64), mock_embedder(dimension=64), 1000, 0)


GEN_JSON = json.dumps([
    {"type": "single", "stem": "光合作用发生在?", "options": ["线粒体", "叶绿体", "核糖体"],
     "answer": 1, "explanation": "叶绿体"},
    {"type": "truefalse", "stem": "光合作用释放氧气", "options": None,
     "answer": True, "explanation": "是"},
], ensure_ascii=False)


async def _completer_returning(text):
    async def c(system, user):
        return text
    return c


@pytest.mark.asyncio
async def test_generate_parses_validates_and_stores(mock_embedder):
    mem = _memory(mock_embedder)
    await mem.add_texts(["光合作用在叶绿体进行，释放氧气"], "knowledge", {"source": "生物"})
    store = QuestionStore(":memory:")
    async def complete(system, user):
        return f"```json\n{GEN_JSON}\n```"       # 带 markdown 围栏
    svc = QuizService(mem, store, complete)
    out = await svc.generate("光合作用", 2, ["single", "truefalse"])
    assert len(out) == 2
    assert all("id" in q for q in out)
    assert {q["type"] for q in out} == {"single", "truefalse"}
    assert len(store.list()) == 2                # 已入库
    assert out[0]["source"] == "光合作用"         # source 记为 topic


@pytest.mark.asyncio
async def test_generate_no_knowledge_raises(mock_embedder):
    mem = _memory(mock_embedder)                 # 空知识库
    svc = QuizService(mem, QuestionStore(":memory:"), lambda s, u: None)
    with pytest.raises(NoKnowledge):
        await svc.generate("任意主题", 2, ["single"])


@pytest.mark.asyncio
async def test_generate_bad_json_raises(mock_embedder):
    mem = _memory(mock_embedder)
    await mem.add_texts(["有内容"], "knowledge", {})
    async def complete(system, user):
        return "这不是 JSON"
    svc = QuizService(mem, QuestionStore(":memory:"), complete)
    with pytest.raises(QuizError):
        await svc.generate("主题", 2, ["single"])


@pytest.mark.asyncio
async def test_generate_filters_invalid_items(mock_embedder):
    mem = _memory(mock_embedder)
    await mem.add_texts(["有内容"], "knowledge", {})
    bad = json.dumps([
        {"type": "single", "stem": "", "options": ["a", "b"], "answer": 0},   # stem 空 → 剔除
        {"type": "single", "stem": "有效吗", "options": ["a", "b"], "answer": 5},  # 索引越界 → 剔除
    ], ensure_ascii=False)
    async def complete(system, user):
        return bad
    svc = QuizService(mem, QuestionStore(":memory:"), complete)
    with pytest.raises(QuizError):               # 全不合法 → QuizError
        await svc.generate("主题", 2, ["single"])

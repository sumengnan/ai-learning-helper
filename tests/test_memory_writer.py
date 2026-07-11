# tests/test_memory_writer.py
from harness.memory.record import MemType
from harness.memory.record import MemoryFilter, MemoryRecord
from harness.memory.reranker import NoOpReranker
from harness.memory.retriever import RetrievalConfig, Retriever
from harness.memory.sqlite_backend import SqliteVecBackend
from harness.memory.writer import ExtractedFact, MemoryWriter, _parse_facts
from harness.memory.writer import MemoryOp, _parse_ops


class ScriptedCompleter:
    """按顺序返回预设响应的假 LLM completer（async (sys, user) -> str）。"""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
    async def __call__(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        return self._responses.pop(0)


def test_parse_facts_valid():
    raw = '[{"text":"用户偏好中文","mem_type":"semantic","entity_key":"user.pref.lang","importance":0.8}]'
    facts = _parse_facts(raw)
    assert len(facts) == 1
    assert facts[0].text == "用户偏好中文"
    assert facts[0].mem_type == MemType.SEMANTIC
    assert facts[0].entity_key == "user.pref.lang"
    assert facts[0].importance == 0.8


def test_parse_facts_code_fence():
    raw = '```json\n[{"text":"事实","mem_type":"episodic"}]\n```'
    facts = _parse_facts(raw)
    assert len(facts) == 1 and facts[0].mem_type == MemType.EPISODIC
    assert facts[0].importance == 0.5 and facts[0].entity_key == ""


def test_parse_facts_invalid_json_returns_empty():
    assert _parse_facts("对不起我不会") == []
    assert _parse_facts('{"not":"a list"}') == []


def test_parse_facts_bad_memtype_defaults_semantic():
    facts = _parse_facts('[{"text":"x","mem_type":"weird"}]')
    assert facts[0].mem_type == MemType.SEMANTIC


async def test_extract_calls_llm_and_parses(mock_embedder):
    comp = ScriptedCompleter(['[{"text":"用户在学 Python","mem_type":"semantic","importance":0.7}]'])
    w = MemoryWriter(backend=None, embedder=mock_embedder(dimension=64),
                     retriever=None, complete=comp)
    facts = await w._extract("我最近在学 Python")
    assert len(facts) == 1 and facts[0].text == "用户在学 Python"
    assert len(comp.calls) == 1


async def test_extract_empty_on_llm_garbage(mock_embedder):
    comp = ScriptedCompleter(["这不是 JSON"])
    w = MemoryWriter(backend=None, embedder=mock_embedder(dimension=64),
                     retriever=None, complete=comp)
    assert await w._extract("闲聊") == []


def _facts(*texts):
    return [ExtractedFact(text=t, mem_type=MemType.SEMANTIC) for t in texts]


def test_parse_ops_resolves_fact_index():
    facts = _facts("f0", "f1")
    ops = _parse_ops('[{"op":"REPLACE","fact_index":0,"supersede_ids":["x"]},'
                     '{"op":"NOOP","fact_index":1}]', facts)
    assert ops[0].op == "REPLACE" and ops[0].fact.text == "f0" and ops[0].supersede_ids == ["x"]
    assert ops[1].op == "NOOP" and ops[1].fact.text == "f1"


def test_parse_ops_uncovered_fact_defaults_add():
    facts = _facts("f0", "f1")
    ops = _parse_ops('[{"op":"NOOP","fact_index":0}]', facts)
    adds = [o for o in ops if o.op == "ADD"]
    assert any(o.fact.text == "f1" for o in adds)


def test_parse_ops_invalid_json_degrades_to_all_add():
    facts = _facts("f0", "f1")
    ops = _parse_ops("不是 JSON", facts)
    assert all(o.op == "ADD" for o in ops) and len(ops) == 2


async def _writer(mock_embedder, responses):
    backend = SqliteVecBackend(":memory:", dimension=64)
    emb = mock_embedder(dimension=64)
    retr = Retriever(backend, emb, NoOpReranker(), RetrievalConfig())
    return MemoryWriter(backend, emb, retr, ScriptedCompleter(responses)), backend, emb


async def test_gather_candidates_by_entity_and_semantic(mock_embedder):
    w, backend, emb = await _writer(mock_embedder, [])
    v = (await emb.embed(["用户偏好深色主题"]))[0]
    backend.upsert([MemoryRecord(owner_id="u1", kind="k", mem_type=MemType.SEMANTIC,
                                 text="用户偏好深色主题", embedding=v, id="old1",
                                 entity_key="user.pref.theme")])
    facts = [ExtractedFact(text="用户偏好深色主题", mem_type=MemType.SEMANTIC,
                           entity_key="user.pref.theme")]
    cands = await w._gather_candidates("u1", "k", facts)
    assert any(c.id == "old1" for c in cands)


async def test_reconcile_no_candidates_all_add(mock_embedder):
    w, backend, emb = await _writer(mock_embedder, [])
    facts = _facts("f0", "f1")
    ops = await w._reconcile(facts, [])
    assert all(o.op == "ADD" for o in ops) and len(ops) == 2

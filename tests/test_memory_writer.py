# tests/test_memory_writer.py
from harness.memory.record import MemType
from harness.memory.writer import ExtractedFact, MemoryWriter, _parse_facts


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

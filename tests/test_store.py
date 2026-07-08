import pytest

from harness.memory.store import MemoryStore, MemoryHit


def _store():
    return MemoryStore(":memory:", dimension=4)


def test_add_and_search_returns_nearest():
    s = _store()
    s.add([
        ("knowledge", "a", {"source": "s1"}, [1.0, 0.0, 0.0, 0.0]),
        ("knowledge", "b", {}, [0.0, 1.0, 0.0, 0.0]),
        ("knowledge", "c", {}, [0.0, 0.0, 1.0, 0.0]),
    ])
    hits = s.search("knowledge", [1.0, 0.0, 0.0, 0.0], k=2)
    assert hits[0].text == "a"                 # 最近
    assert hits[0].metadata["source"] == "s1"
    assert len(hits) == 2
    assert isinstance(hits[0], MemoryHit)


def test_collection_isolation():
    s = _store()
    s.add([
        ("knowledge", "k1", {}, [1.0, 0.0, 0.0, 0.0]),
        ("notes", "n1", {}, [1.0, 0.0, 0.0, 0.0]),
    ])
    hits = s.search("notes", [1.0, 0.0, 0.0, 0.0], k=5)
    assert [h.text for h in hits] == ["n1"]    # 不串 collection


def test_delete():
    s = _store()
    ids = s.add([("knowledge", "x", {}, [1.0, 0.0, 0.0, 0.0])])
    s.delete(ids)
    assert s.search("knowledge", [1.0, 0.0, 0.0, 0.0], k=5) == []

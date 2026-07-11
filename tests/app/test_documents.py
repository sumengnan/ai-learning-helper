from app.documents import DocumentStore


def test_create_list_chunkids_delete():
    s = DocumentStore(":memory:")
    s.create("u1", "d1", "bio.txt", 123, [1, 2, 3])
    docs = s.list("u1")
    assert docs[0]["id"] == "d1" and docs[0]["num_chunks"] == 3
    assert s.chunk_ids("u1", "d1") == [1, 2, 3]
    assert s.exists("u1", "d1") is True
    s.delete("u1", "d1")
    assert s.exists("u1", "d1") is False and s.list("u1") == []


def test_remove_chunk_syncs_doc():
    s = DocumentStore(":memory:")
    s.create("u1", "d1", "bio.txt", 123, ["c1", "c2", "c3"])
    # 删中间一片：chunk_ids 与 num_chunks 同步
    s.remove_chunk("u1", "d1", "c2")
    assert s.chunk_ids("u1", "d1") == ["c1", "c3"]
    assert s.list("u1")[0]["num_chunks"] == 2
    # 删到空：文档行随之删除
    s.remove_chunk("u1", "d1", "c1")
    s.remove_chunk("u1", "d1", "c3")
    assert s.exists("u1", "d1") is False
    # 删不存在的片段：静默无副作用
    s.remove_chunk("u1", "d1", "c9")

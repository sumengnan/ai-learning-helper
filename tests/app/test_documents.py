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

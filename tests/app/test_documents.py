from app.documents import DocumentStore


def test_create_list_chunkids_delete():
    s = DocumentStore(":memory:")
    s.create("d1", "bio.txt", 123, [1, 2, 3])
    docs = s.list()
    assert docs[0]["id"] == "d1" and docs[0]["num_chunks"] == 3
    assert s.chunk_ids("d1") == [1, 2, 3]
    assert s.exists("d1") is True
    s.delete("d1")
    assert s.exists("d1") is False and s.list() == []

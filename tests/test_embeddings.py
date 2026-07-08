import pytest

from harness.memory.embeddings import OpenAICompatibleEmbeddingClient, EmbeddingClient


async def test_mock_embedder_is_deterministic_and_shaped(mock_embedder):
    emb = mock_embedder(dimension=64)
    a = await emb.embed(["cat dog"])
    b = await emb.embed(["cat dog"])
    assert a == b
    assert len(a[0]) == 64


async def test_openai_embedding_client_calls_api(monkeypatch):
    client = OpenAICompatibleEmbeddingClient(
        base_url="http://x/v1", api_key="k", model="m", dimension=3)

    class _D:
        def __init__(self, index, e):
            self.index = index
            self.embedding = e

    class _Resp:
        data = [_D(0, [1.0, 2.0, 3.0]), _D(1, [4.0, 5.0, 6.0])]

    async def fake_create(model, input):
        assert model == "m"
        assert input == ["a", "b"]
        return _Resp()

    monkeypatch.setattr(client._client.embeddings, "create", fake_create)
    out = await client.embed(["a", "b"])
    assert out == [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
    assert client.dimension == 3


async def test_openai_embedding_client_sorts_by_index(monkeypatch):
    client = OpenAICompatibleEmbeddingClient(
        base_url="http://x/v1", api_key="k", model="m", dimension=3)

    class _D:
        def __init__(self, index, e):
            self.index = index
            self.embedding = e

    class _Resp:
        # 乱序返回：index 1 在前，index 0 在后
        data = [_D(1, [4.0, 5.0, 6.0]), _D(0, [1.0, 2.0, 3.0])]

    async def fake_create(model, input):
        return _Resp()

    monkeypatch.setattr(client._client.embeddings, "create", fake_create)
    out = await client.embed(["a", "b"])
    assert out == [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]   # 按 index 归位

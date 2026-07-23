"""接线：温度真的发到了请求体上，且各调用点用的是自己那档。

机制层单测（tests/test_sampling.py）只证明 resolve_sampling 算得对；这里证明它确实被
OpenAICompatibleClient 用上了，以及 with_role / 意图路由 / 重试增量确实落到那次调用上。
"""
import pytest

from harness.config import HarnessConfig
from harness.llm.base import StreamChunk
from harness.llm.openai_compat import OpenAICompatibleClient
from harness.llm.sampling import sampling, temperature_delta
from harness.types import Message, Role


class _FakeStream:
    """够用的 AsyncOpenAI 替身：记下 create() 收到的 kwargs，产出一条空流。"""
    def __init__(self, sink):
        self._sink = sink
        self.chat = self

    @property
    def completions(self):
        return self

    async def create(self, **kwargs):
        self._sink.update(kwargs)

        async def _gen():
            return
            yield   # pragma: no cover - 只为让它成为 async generator

        return _gen()


def _client(sink, **cfg_kw):
    cfg = HarnessConfig(api_key="k", model=cfg_kw.pop("model", "qwen-plus"), **cfg_kw)
    c = OpenAICompatibleClient(cfg)
    c._client = _FakeStream(sink)
    return c


async def _run(client):
    async for _ in client.stream([Message(role=Role.USER, content="hi")], []):
        pass


async def test_config_temperature_is_sent_by_default():
    sink = {}
    await _run(_client(sink, temperature=0.7))
    assert sink["temperature"] == 0.7


async def test_role_override_reaches_the_request():
    sink = {}
    client = _client(sink, temperature=0.7)
    with sampling(temperature=0.0):          # 判分档
        await _run(client)
    assert sink["temperature"] == 0.0


async def test_retry_delta_reaches_the_request():
    sink = {}
    client = _client(sink, temperature=0.7)
    with sampling(temperature=0.2), temperature_delta(0.15):
        await _run(client)
    assert sink["temperature"] == pytest.approx(0.35)


async def test_temperature_never_exceeds_one_on_the_wire():
    """端到端的边界保证：不管怎么叠加，发出去的值都在 [0,1]。"""
    sink = {}
    client = _client(sink, temperature=1.0)
    with temperature_delta(0.9):
        await _run(client)
    assert sink["temperature"] == 1.0


async def test_top_p_replaces_temperature_on_the_wire():
    sink = {}
    client = _client(sink, temperature=0.7)
    with sampling(top_p=0.8):
        await _run(client)
    assert sink["top_p"] == 0.8
    assert "temperature" not in sink


async def test_unsupported_model_gets_no_sampling_params():
    """豁免名单命中 → 一个采样参数都不发，避免端点因未知参数报错。"""
    sink = {}
    client = _client(sink, model="o1-preview", sampling_unsupported_models=["o1-"])
    with sampling(temperature=0.3):
        await _run(client)
    assert "temperature" not in sink and "top_p" not in sink


async def test_config_temperature_clamped_at_load():
    """.env 里写了 1.8 不会带到线上：入口就夹掉并告警。"""
    assert HarnessConfig(api_key="k", temperature=1.8).temperature == 1.0

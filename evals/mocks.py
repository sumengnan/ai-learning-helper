# evals/mocks.py
"""eval 自己的确定性替身。

为什么不复用 tests/conftest.py 的 MockEmbeddingClient：conftest 不可被外部 import
（无 __init__.py + --import-mode=importlib），而 evals/ 又绝不能反过来 import tests/。
若 CLI 和 CI 各用一份 embedder，两边算出的分数会不一致 —— 基线比对的前提「同样输入
必得同样分数」当场失效。故这里独占一份，CI（tests/evals/）与 CLI 都从这里取，保证同源。

与 conftest.MockEmbeddingClient 算法相同但各自独立演进：那个服务 130 个内核/应用测试，
这个只决定 eval 分数。内核测试不该 import 评测工具包，故不做单一来源合并。
"""
from __future__ import annotations

import hashlib
import math


class HashEmbedder:
    """确定性 embedder：按空白分词哈希到固定维度并归一化。共享词的文本向量更接近。不打网络。"""

    def __init__(self, dimension: int = 64) -> None:
        self.dimension = dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dimension
        for token in text.lower().split():
            h = int(hashlib.md5(token.encode()).hexdigest(), 16)
            v[h % self.dimension] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

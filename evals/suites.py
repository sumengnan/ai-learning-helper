# evals/suites.py
"""套件注册表：套件名 → (cases, driver, scorers)。

CI 门禁（tests/evals/）与 CLI（evals/cli.py）都从这里组装，绝不各写一份 —— 两边若用
不同的 driver/scorer/embedder，算出的分数就不可比，基线门禁立刻失去意义。
"""
from __future__ import annotations

from .dataset import load_corpus, load_suite
from .drivers import ExamGradeDriver, GateDriver, RetrievalDriver
from .mocks import HashEmbedder
from .scorers import ExamGradeScorer, GateScorer, RetrievalScorer

# 组件层：全 mock、零网络、确定性 → 进 CI 当门禁
COMPONENT = ("exam_grade", "gate", "retrieval")


def build(suite: str):
    """返回 (cases, driver, scorers)。"""
    cases = load_suite(suite)
    if suite == "exam_grade":
        return cases, ExamGradeDriver(), [ExamGradeScorer()]
    if suite == "gate":
        return cases, GateDriver(), [GateScorer()]
    if suite == "retrieval":
        # 语料集在 case 里声明；同一套件内混用多个语料尚无需求，故取第一条的即可
        corpus = load_corpus(cases[0].input.corpus if cases else "default")
        driver = RetrievalDriver(embedder=HashEmbedder(64), corpus=corpus)
        return cases, driver, [RetrievalScorer("hit@k"), RetrievalScorer("mrr")]
    raise ValueError(f"未知套件 {suite!r}；可用：{', '.join(COMPONENT)}")

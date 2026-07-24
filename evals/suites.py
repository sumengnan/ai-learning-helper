# evals/suites.py
"""套件注册表：套件名 → (cases, driver, scorers)。

CI 门禁（tests/evals/）与 CLI（evals/cli.py）都从这里组装，绝不各写一份 —— 两边若用
不同的 driver/scorer/embedder，算出的分数就不可比，基线门禁立刻失去意义。
"""
from __future__ import annotations

from .dataset import load_corpus, load_suite
from .drivers import AgentDriver, ChecksDriver, ExamGradeDriver, RetrievalDriver
from .mocks import HashEmbedder
from .scorers import (ChecksScorer, ContainsScorer, ExamGradeScorer, LlmJudgeScorer,
                      RetrievalScorer, ToolCallScorer)

# 组件层：全 mock、零网络、确定性 → 进 CI 当门禁
COMPONENT = ("exam_grade", "checks", "retrieval")
# 端到端层：真实 API + LLM judge，慢、花钱、天生 flaky → 只手动跑，永不当 PR 门禁
REAL = ("agent",)
ALL = COMPONENT + REAL


def build(suite: str):
    """返回 (cases, driver, scorers)。agent 套件会打真实端点、花钱。"""
    cases = load_suite(suite)
    if suite == "exam_grade":
        return cases, ExamGradeDriver(), [ExamGradeScorer()]
    if suite == "checks":
        return cases, ChecksDriver(), [ChecksScorer()]
    if suite == "retrieval":
        # 语料集在 case 里声明；同一套件内混用多个语料尚无需求，故取第一条的即可
        corpus = load_corpus(cases[0].input.corpus if cases else "default")
        driver = RetrievalDriver(embedder=HashEmbedder(64), corpus=corpus)
        return cases, driver, [RetrievalScorer("hit@k"), RetrievalScorer("mrr")]
    if suite == "agent":
        return (cases, *_real_agent())
    raise ValueError(f"未知套件 {suite!r}；可用：{', '.join(ALL)}")


def _real_agent():
    """组装打真实端点的 agent driver + 打分器。

    延迟 import：组件层不该为了跑几个脚本化 case 而拉起整个 app 装配。
    刻意不开 sandbox/browser/dispatch/skills/mcp（AppConfig 里它们默认就是 False）——
    eval 要的是可比的分数，多一个外部依赖就多一份与模型质量无关的方差。
    """
    from app.assembly import build_harness
    from app.completion import build_judge_completer
    from app.config import AppConfig

    from .judge import StrictJudge

    cfg = AppConfig()
    if not cfg.api_key:
        raise RuntimeError("agent 套件要打真实端点，需在 .env 配 HARNESS_API_KEY；"
                           "只想验管线没坏的话跑 `uv run pytest tests/evals`（全 mock）")
    h = build_harness(cfg)
    driver = AgentDriver(client=h.client, registry=h.registry,
                         system_prompt=h.system_prompt, max_steps=cfg.max_steps,
                         model_name=cfg.model)
    # 独立裁判模型（配了 HARNESS_JUDGE_MODEL 就用它，否则回退主模型）可降低「自己给自己
    # 打高分」的偏差；judge_samples > 1 时多次采样取中位数压方差。
    judge = StrictJudge(build_judge_completer(h.client, cfg), samples=cfg.judge_samples)
    return driver, [ContainsScorer(), ToolCallScorer(), LlmJudgeScorer(judge)]

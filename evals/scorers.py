# evals/scorers.py
"""打分器。Score 的三态是整个 eval 系统的语义地基。

与线上（app/verify.py）的关键差异：线上「LLM/基建失败 → 吞掉当通过」，绝不因抖动拦交付；
eval 必须相反 —— 失败要显式变成 status="error"，否则一次端点抖动会被读成一次满分。
聚合时 skipped/error 既不进分子也不进分母（见 report.py::SuiteReport.metrics）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

OK = "ok"
SKIPPED = "skipped"        # 该 case 不适用此打分器（如 expect 没写期望）
ERROR = "error"            # 跑挂了 —— 是「没跑成」，不是「不及格」


@dataclass
class Score:
    value: float               # 0.0~1.0
    status: str = OK
    detail: str = ""           # 人读的一行说明（失败时说清实得 vs 期望）
    raw: dict | None = None


class Scorer(Protocol):
    name: str
    async def score(self, case, output) -> Score: ...


class ExamGradeScorer:
    """比对 exam_grader 的解析/判分产物与期望。

    expect 的 parsed/correct 都是「写了才校验」—— None 本身是合法期望值
    （parsed=None 表示期望解析不出/有歧义），故靠 model_fields_set 而非 None 判断。
    """

    name = "exam_grade"

    async def score(self, case, output: dict) -> Score:
        written = case.expect.model_fields_set
        checks: list[tuple[str, Any, Any]] = []
        if "parsed" in written:
            checks.append(("parsed", output.get("parsed"), case.expect.parsed))
        if "correct" in written:
            checks.append(("correct", output.get("correct"), case.expect.correct))
        if not checks:
            return Score(0.0, SKIPPED, "expect 里没写 parsed/correct，无可校验")
        bad = [f"{k} 实得 {got!r}、期望 {want!r}" for k, got, want in checks if got != want]
        return Score(0.0 if bad else 1.0, OK, "；".join(bad))


class GateScorer:
    """比对 AnswerVerifier.verify 的 Verdict 与期望。failed 用子集匹配（不要求穷举）。"""

    name = "gate"

    async def score(self, case, output) -> Score:
        bad: list[str] = []
        if output.ok != case.expect.ok:
            bad.append(f"ok 实得 {output.ok}、期望 {case.expect.ok}")
        missing = [x for x in case.expect.failed if x not in output.failed]
        if missing:
            bad.append(f"期望未通过的层 {missing} 不在实得 failed={output.failed} 中")
        return Score(0.0 if bad else 1.0, OK, "；".join(bad))


class RetrievalScorer:
    """取 harness.memory.eval.evaluate 算出的单条指标（hit@k / mrr）。"""

    def __init__(self, metric: str) -> None:
        self.name = metric

    async def score(self, case, output: dict) -> Score:
        if self.name not in output:
            return Score(0.0, ERROR, f"driver 未产出指标 {self.name}，实得键 {list(output)}")
        return Score(float(output[self.name]), OK)

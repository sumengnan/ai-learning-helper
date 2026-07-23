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


class ContainsScorer:
    """确定性断言：must_contain 全中且 must_not_contain 全不中才算满分。

    先跑它再跑 LLM judge —— 便宜、确定、无歧义的检查不该交给模型。
    """

    name = "contains"

    async def score(self, case, trace) -> Score:
        exp = case.expect
        if not exp.must_contain and not exp.must_not_contain:
            return Score(0.0, SKIPPED, "未声明 must_contain / must_not_contain")
        if trace.error:
            return Score(0.0, ERROR, f"agent 跑挂：{trace.error}")
        text = trace.final or ""
        missing = [s for s in exp.must_contain if s not in text]
        present = [s for s in exp.must_not_contain if s in text]
        bad = []
        if missing:
            bad.append(f"答案里缺 {missing}")
        if present:
            bad.append(f"答案里不该出现 {present}")
        return Score(0.0 if bad else 1.0, OK, "；".join(bad))


class ToolCallScorer:
    """轨迹断言：期望被调的工具是否真的调过。"""

    name = "tool_call"

    async def score(self, case, trace) -> Score:
        want = case.expect.must_call_tools
        if not want:
            return Score(0.0, SKIPPED, "未声明 must_call_tools")
        if trace.error:
            return Score(0.0, ERROR, f"agent 跑挂：{trace.error}")
        missing = [t for t in want if t not in trace.tools]
        return Score(0.0 if missing else 1.0, OK,
                     f"未调用 {missing}（实际调了 {trace.tools or '无'}）" if missing else "")


class LlmJudgeScorer:
    """独立裁判模型给答案质量打分。judge 不可用 → ERROR（不是 0 分，也不是放行）。"""

    name = "llm_judge"

    def __init__(self, judge) -> None:
        self._judge = judge          # evals.judge.StrictJudge

    async def score(self, case, trace) -> Score:
        if trace.error:
            return Score(0.0, ERROR, f"agent 跑挂：{trace.error}")
        return await self._judge.score_answer(
            case.input.message, trace.final, trace.steps, case.expect.rubric)


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


class ChecksScorer:
    """比对 DeliveryChecker.run 产出的提醒与期望。

    期望列表用子集匹配（不要求穷举）；但**期望为空时要求实得也为空**——「不该提醒却提醒了」
    是这套东西最要命的失败模式（假提醒会让用户学会无视全部提醒），必须能被这套 case 抓住。
    """

    name = "checks"

    async def score(self, case, output) -> Score:
        got = [n.kind for n in output]
        bad: list[str] = []
        missing = [x for x in case.expect.notices if x not in got]
        if missing:
            bad.append(f"期望的提醒 {missing} 不在实得 {got} 中")
        if not case.expect.notices and got:
            bad.append(f"不该产生提醒，实得 {got}")
        return Score(0.0 if bad else 1.0, OK, "；".join(bad))


class RetrievalScorer:
    """取 harness.memory.eval.evaluate 算出的单条指标（hit@k / mrr）。"""

    def __init__(self, metric: str) -> None:
        self.name = metric

    async def score(self, case, output: dict) -> Score:
        if self.name not in output:
            return Score(0.0, ERROR, f"driver 未产出指标 {self.name}，实得键 {list(output)}")
        return Score(float(output[self.name]), OK)

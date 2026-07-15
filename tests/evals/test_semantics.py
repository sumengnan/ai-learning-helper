"""eval 系统自身的元测试：三态语义 + 门禁判据。

这里守的是整套设计的地基 —— 「跑挂了」必须区别于「不及格」。线上（app/verify.py）
的约定是失败即放行，eval 若照搬，一次端点抖动就会被读成一次满分。
"""
import pytest

from evals.baseline import Diff, compare, gate
from evals.dataset import load_jsonl
from evals.report import CaseResult, SuiteReport
from evals.runner import run_suite
from evals.schema import ExamGradeCase
from evals.scorers import ERROR, OK, SKIPPED, ExamGradeScorer, Score


class _BoomDriver:
    async def run(self, case):
        raise RuntimeError("端点挂了")


class _OkDriver:
    async def run(self, case):
        return {"parsed": 1, "correct": True}


def _case(cid="c1", **expect):
    return ExamGradeCase(id=cid,
                         input={"question": {"type": "single", "stem": "s",
                                             "options": ["a", "b"], "answer": 1},
                                "user_text": "B"},
                         expect=expect)


# ---- 三态：跑挂 ≠ 不及格 ----

async def test_driver_failure_is_error_not_zero_score():
    report = await run_suite([_case(correct=True)], _BoomDriver(), [ExamGradeScorer()],
                             suite="t")
    m = report.metrics()["exam_grade"]
    assert m["errors"] == 1
    assert m["n"] == 0, "跑挂的 case 不该进分母"
    assert report.error_rate() == 1.0


async def test_error_does_not_drag_mean_down():
    """一个跑挂 + 一个满分 → 均值仍是 1.0，而不是被摊成 0.5。

    这是与线上语义分道扬镳的核心：抖动不该伪装成质量变化（无论变好还是变坏）。
    """
    cases = [_case("ok1", correct=True), _case("boom", correct=True)]

    class _Flaky:
        async def run(self, case):
            if case.id == "boom":
                raise RuntimeError("抖了一下")
            return {"parsed": 1, "correct": True}

    report = await run_suite(cases, _Flaky(), [ExamGradeScorer()], suite="t")
    m = report.metrics()["exam_grade"]
    assert m["mean"] == 1.0 and m["n"] == 1 and m["errors"] == 1


async def test_case_error_does_not_abort_the_suite():
    cases = [_case("boom"), _case("ok1", correct=True)]

    class _Flaky:
        async def run(self, case):
            if case.id == "boom":
                raise RuntimeError("抖了一下")
            return {"parsed": 1, "correct": True}

    report = await run_suite(cases, _Flaky(), [ExamGradeScorer()], suite="t")
    assert len(report.cases) == 2, "单个 case 跑挂不该中断整轮"


async def test_no_expectation_is_skipped_not_passed():
    report = await run_suite([_case()], _OkDriver(), [ExamGradeScorer()], suite="t")
    m = report.metrics()["exam_grade"]
    assert m["skipped"] == 1 and m["n"] == 0, "没写期望 → 跳过，不能算通过"


# ---- 门禁判据 ----

def _report_with(mean_scores: list[Score]) -> SuiteReport:
    r = SuiteReport(suite="t")
    r.cases = [CaseResult(case_id=f"c{i}", kind="exam_grade", scores={"exam_grade": s})
               for i, s in enumerate(mean_scores)]
    return r


def test_gate_flags_high_error_rate_as_invalid_not_failing():
    r = _report_with([Score(0.0, ERROR), Score(1.0, OK)])
    ok, why = gate(r, [], max_error_rate=0.1)
    assert ok is False
    assert "没跑成" in why, "高错误率要说清是「没跑成」而非「不及格」"


def test_gate_passes_when_at_baseline():
    r = _report_with([Score(1.0, OK)])
    diffs = compare(r, {"metrics": {"exam_grade": {"mean": 1.0, "n": 1}}})
    ok, _ = gate(r, diffs)
    assert ok is True


def test_gate_fails_on_regression():
    r = _report_with([Score(0.0, OK), Score(1.0, OK)])     # mean 0.5
    diffs = compare(r, {"metrics": {"exam_grade": {"mean": 1.0, "n": 2}}})
    ok, why = gate(r, diffs)
    assert ok is False and "1.000 → 0.500" in why


def test_gate_flags_vanished_metric_as_regression():
    """打分器被删/改名 → 基线里的指标消失，必须红而不是静默放行。"""
    r = _report_with([Score(1.0, OK)])
    diffs = compare(r, {"metrics": {"某个已删的打分器": {"mean": 1.0, "n": 1}}})
    assert diffs[0].regressed is True


def test_improvement_passes_but_is_reported():
    r = _report_with([Score(1.0, OK)])
    diffs = compare(r, {"metrics": {"exam_grade": {"mean": 0.5, "n": 1}}})
    ok, why = gate(r, diffs)
    assert ok is True and "变好" in why


# ---- 数据集加载 ----

def test_duplicate_id_is_rejected(tmp_path):
    p = tmp_path / "d.jsonl"
    line = ('{"id":"x","kind":"retrieval","input":{"query":"q"},'
            '"expect":{"relevant_ids":["d1"]}}')
    p.write_text(f"{line}\n{line}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="重复的 case id"):
        load_jsonl(p)


def test_bad_line_reports_line_number(tmp_path):
    p = tmp_path / "d.jsonl"
    good = ('{"id":"x","kind":"retrieval","input":{"query":"q"},'
            '"expect":{"relevant_ids":["d1"]}}')
    p.write_text(f"{good}\n{{\"id\":\"y\",\"kind\":\"nope\"}}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r":2 "):
        load_jsonl(p)


def test_comments_and_blank_lines_skipped(tmp_path):
    p = tmp_path / "d.jsonl"
    p.write_text('# 注释\n\n{"id":"x","kind":"retrieval","input":{"query":"q"},'
                 '"expect":{"relevant_ids":["d1"]}}\n', encoding="utf-8")
    assert len(load_jsonl(p)) == 1

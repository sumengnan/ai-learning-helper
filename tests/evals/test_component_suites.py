"""组件级 eval 套件的 CI 门禁：固定数据集 + mock，零网络、秒级、确定性。

与 evals/cli.py 走完全相同的 suites.build() → run_suite()，只是断言换成「不得低于基线」。
mock 层完全确定性，所以这里用硬比对而非统计阈值 —— 结构上不可能 flaky。
"""
import pytest

from evals import baseline as bl
from evals import suites as sx
from evals.report import render
from evals.runner import run_suite


@pytest.mark.parametrize("suite", sx.COMPONENT)
async def test_component_suite_not_regressed(suite):
    base = bl.load(suite)
    # 防呆：基线文件被删/改名时必须红。否则 gate() 会以「无基线可比」放行，
    # 门禁静默失效而没人发现。
    assert base is not None, (
        f"套件 {suite} 缺基线文件 {bl.path_for(suite)}；"
        f"跑 `uv run python -m evals update-baseline --suite {suite}` 生成并提交")

    cases, driver, scorers = sx.build(suite)
    report = await run_suite(cases, driver, scorers, suite=suite)

    # 组件层全是脚本化替身，不该有任何 case 跑挂；跑挂说明 driver/数据集坏了，
    # 而不是「质量下降」—— 单独断言以免被 gate 的 error_rate 阈值糊过去。
    assert report.error_rate() == 0.0, f"有 case 跑挂了：\n{render(report)}"

    ok, why = bl.gate(report, bl.compare(report, base))
    assert ok, f"{why}\n\n{render(report)}"

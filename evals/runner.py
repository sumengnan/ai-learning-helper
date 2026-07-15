# evals/runner.py
"""编排：cases × driver × scorers → SuiteReport。

单个 case 跑挂不中断整轮 —— 记成该 case 全部打分器 status=ERROR，继续跑下一个。
一次端点抖动不该让整轮白跑，但也绝不能被静默当成通过（见 scorers.py 模块头）。
"""
from __future__ import annotations

import asyncio
import time

from .report import CaseResult, SuiteReport, git_sha, now_iso
from .scorers import ERROR, Score, Scorer


async def _run_one(case, driver, scorers: list[Scorer], sem: asyncio.Semaphore) -> CaseResult:
    res = CaseResult(case_id=case.id, kind=case.kind, tags=list(case.tags))
    t0 = time.monotonic()
    async with sem:
        try:
            output = await driver.run(case)
        except Exception as e:                      # driver 挂了 → 该 case 全部打分器记 ERROR
            res.error = f"{type(e).__name__}: {e}"
            res.scores = {s.name: Score(0.0, ERROR, res.error) for s in scorers}
            res.elapsed_ms = int((time.monotonic() - t0) * 1000)
            return res
        for s in scorers:
            try:
                res.scores[s.name] = await s.score(case, output)
            except Exception as e:                  # 打分器自己挂了 → 同样是 ERROR，不是 0 分
                res.scores[s.name] = Score(0.0, ERROR, f"打分器异常 {type(e).__name__}: {e}")
    res.elapsed_ms = int((time.monotonic() - t0) * 1000)
    return res


async def run_suite(cases, driver, scorers: list[Scorer], *, suite: str,
                    concurrency: int = 1, model: str = "") -> SuiteReport:
    """跑一遍套件。默认串行 —— 真实层受端点限流，mock 层本来就是毫秒级。"""
    sem = asyncio.Semaphore(max(1, concurrency))
    report = SuiteReport(suite=suite, started_at=now_iso(), git_sha=git_sha(), model=model)
    report.cases = list(await asyncio.gather(
        *(_run_one(c, driver, scorers, sem) for c in cases)))
    return report

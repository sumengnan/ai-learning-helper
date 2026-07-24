# evals/cli.py
"""命令行入口。标准库 argparse，不引 click/typer。

  uv run python -m evals run --suite component
  uv run python -m evals run --suite checks
  uv run python -m evals update-baseline --suite component
  uv run python -m evals compare --report evals/reports/x.json --suite checks
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from . import baseline as bl
from . import suites as sx
from .report import SuiteReport, load_json, render, write_json
from .runner import run_suite


def _expand(name: str) -> list[str]:
    if name == "component":
        return list(sx.COMPONENT)
    if name == "all":
        return list(sx.ALL)
    return [name]


def _check_real(suites: list[str], real: bool) -> None:
    """要打真实端点的套件必须显式 --real —— 花钱的事不能手滑触发。"""
    need = [s for s in suites if s in sx.REAL]
    if need and not real:
        raise SystemExit(f"套件 {', '.join(need)} 会打真实端点、花钱、且慢。"
                         f"确认后加 --real 再跑。")


async def _run_one(suite: str, concurrency: int, limit: int = 0) -> SuiteReport:
    try:
        cases, driver, scorers = sx.build(suite)
    except FileNotFoundError:
        # load_suite 找不到 <套件名>.jsonl —— 多半是套件名打错了
        raise SystemExit(f"✗ 未知套件 {suite!r}；可用：{', '.join(sx.ALL)}"
                         f"（或 component / all）") from None
    except (RuntimeError, ValueError) as e:
        # 配置/环境问题（如缺 key）是使用错误，给一行人话即可，不必甩栈
        raise SystemExit(f"✗ {e}") from None
    if limit:
        cases = cases[:limit]
    return await run_suite(cases, driver, scorers, suite=suite, concurrency=concurrency)


async def _cmd_run(args) -> int:
    suites = _expand(args.suite)
    _check_real(suites, args.real)
    failed = False
    for suite in suites:
        report = await _run_one(suite, args.concurrency, args.limit)
        print(render(report))
        if not args.no_save:
            print(f"报告已写入 {write_json(report)}")
        ok, why = bl.gate(report, bl.compare(report, bl.load(suite)),
                          max_error_rate=args.max_error_rate)
        print(f"{'✓' if ok else '✗'} {suite}：{why}\n")
        failed |= not ok
    return 1 if failed else 0


async def _cmd_update_baseline(args) -> int:
    suites = _expand(args.suite)
    _check_real(suites, args.real)
    for suite in suites:
        report = await _run_one(suite, args.concurrency, args.limit)
        print(render(report))
        if report.error_rate() > 0:
            print(f"✗ {suite}：本轮有跑挂的 case，拒绝把它固化成基线", file=sys.stderr)
            return 1
        print(f"✓ 基线已更新 {bl.save(report, suite)}\n")
    return 0


def _cmd_compare(args) -> int:
    data = load_json(args.report)
    suite = args.suite or data.get("suite", "")
    base = bl.load(suite)
    if not base:
        print(f"✗ 套件 {suite!r} 没有基线可比", file=sys.stderr)
        return 1
    # 从落盘报告重建一个只带 metrics 的壳，够 compare 用
    shell = SuiteReport(suite=suite)
    shell.metrics = lambda: data["metrics"]              # type: ignore[method-assign]
    shell.error_rate = lambda: data.get("error_rate", 0.0)   # type: ignore[method-assign]
    diffs = bl.compare(shell, base)
    for d in diffs:
        mark = "✗" if d.regressed else ("↑" if d.delta > 0 else "=")
        print(f"{mark} {d.metric:<16}{d.baseline:.3f} → {d.current:.3f}  ({d.delta:+.3f})")
    ok, why = bl.gate(shell, diffs)
    print(f"\n{'✓' if ok else '✗'} {why}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m evals", description="离线评测")
    sub = p.add_subparsers(dest="cmd", required=True)

    def _common(sp):
        sp.add_argument("--suite", required=True,
                        help=f"套件名（{' / '.join(sx.ALL)}），或 component（组件层全跑）/ all")
        sp.add_argument("--concurrency", type=int, default=1)
        sp.add_argument("--limit", type=int, default=0, help="只跑前 N 个 case（冒烟用）")
        sp.add_argument("--real", action="store_true",
                        help=f"允许打真实端点（{' / '.join(sx.REAL)} 套件必须显式加）")

    r = sub.add_parser("run", help="跑套件并与基线比对")
    _common(r)
    r.add_argument("--no-save", action="store_true", help="不写报告文件")
    r.add_argument("--max-error-rate", type=float, default=0.1)

    u = sub.add_parser("update-baseline", help="把本轮结果固化成基线")
    _common(u)

    c = sub.add_parser("compare", help="拿已落盘的报告与基线比对")
    c.add_argument("--report", required=True)
    c.add_argument("--suite", default="")

    args = p.parse_args(argv)
    if args.cmd == "run":
        return asyncio.run(_cmd_run(args))
    if args.cmd == "update-baseline":
        return asyncio.run(_cmd_update_baseline(args))
    return _cmd_compare(args)

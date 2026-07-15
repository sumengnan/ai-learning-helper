# evals/report.py
"""跑一轮的结果结构 + 落盘 + 终端摘要。

报告用 JSON 文件而非 SQLite：可 diff、可当 CI artifact、可直接喂 baseline.compare、
零 schema 迁移。真要画多次运行的趋势时，再加个 append_sqlite 是纯加法。
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .scorers import ERROR, OK, SKIPPED, Score

_REPORTS = Path(__file__).parent / "reports"


def git_sha() -> str:
    """当前 commit 短 sha；非 git 环境返回空串（报告仍可用，只是少一个溯源字段）。"""
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CaseResult:
    case_id: str
    kind: str
    tags: list[str] = field(default_factory=list)
    scores: dict[str, Score] = field(default_factory=dict)
    elapsed_ms: int = 0
    error: str = ""            # driver 抛异常 → 该 case 全部打分器记 ERROR


@dataclass
class SuiteReport:
    suite: str
    started_at: str = ""
    git_sha: str = ""
    model: str = ""
    cases: list[CaseResult] = field(default_factory=list)

    def metrics(self) -> dict[str, dict]:
        """按打分器聚合。skipped/error 既不进分子也不进分母，单独计数。

        这是「端点挂了不能被读成质量满分」在数据层的兑现 —— 分母只含真正跑成的 case。
        """
        out: dict[str, dict] = {}
        for case in self.cases:
            for name, sc in case.scores.items():
                m = out.setdefault(name, {"mean": 0.0, "n": 0, "skipped": 0, "errors": 0,
                                          "_sum": 0.0})
                if sc.status == OK:
                    m["n"] += 1
                    m["_sum"] += sc.value
                elif sc.status == SKIPPED:
                    m["skipped"] += 1
                else:
                    m["errors"] += 1
        for m in out.values():
            m["mean"] = round(m["_sum"] / m["n"], 6) if m["n"] else 0.0
            del m["_sum"]
        return out

    def error_rate(self) -> float:
        """跑挂的打分次数占比。高于阈值意味着这轮「没跑成」，而不是「不及格」。"""
        total = errors = 0
        for case in self.cases:
            for sc in case.scores.values():
                total += 1
                errors += sc.status == ERROR
        return errors / total if total else 0.0

    def failures(self) -> list[tuple[str, str, Score]]:
        """(case_id, scorer_name, score) 列表，只含真跑成但没满分的，供终端展示。"""
        return [(c.case_id, n, s) for c in self.cases for n, s in c.scores.items()
                if s.status == OK and s.value < 1.0]

    def to_dict(self) -> dict:
        return {"suite": self.suite, "started_at": self.started_at, "git_sha": self.git_sha,
                "model": self.model, "metrics": self.metrics(),
                "error_rate": round(self.error_rate(), 6),
                "cases": [asdict(c) for c in self.cases]}


def write_json(report: SuiteReport, out_dir: str | Path = _REPORTS) -> Path:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    stamp = report.started_at.replace(":", "").replace("-", "")[:15]
    p = d / f"{report.suite}-{stamp}.json"
    p.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def render(report: SuiteReport) -> str:
    """终端一屏摘要。"""
    lines = [f"套件 {report.suite}  模型 {report.model or '—'}  commit {report.git_sha or '—'}",
             f"{'打分器':<16}{'均值':>8}{'样本':>6}{'跳过':>6}{'出错':>6}"]
    for name, m in sorted(report.metrics().items()):
        lines.append(f"{name:<16}{m['mean']:>8.3f}{m['n']:>6}{m['skipped']:>6}{m['errors']:>6}")
    fails = report.failures()
    if fails:
        lines.append(f"\n未满分的 case（{len(fails)}）：")
        lines += [f"  - {cid} [{name}] {sc.value:.2f} {sc.detail}" for cid, name, sc in fails]
    errs = [(c.case_id, c.error) for c in report.cases if c.error]
    if errs:
        lines.append(f"\n⚠️ 跑挂的 case（{len(errs)}）—— 这些不计入均值：")
        lines += [f"  - {cid}: {e}" for cid, e in errs]
    return "\n".join(lines)

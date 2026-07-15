# evals/baseline.py
"""基线：门禁的判据。

mock 层完全确定性（脚本化 LLM + hash embedding + 固定数据集文件，无随机采样），
所以门禁用「current >= baseline」硬比对，而不是统计阈值 —— 结构上不可能 flaky。

基线文件进 git，且只存 metrics（不存时间戳/sha/逐 case 明细）：这样 update-baseline
的 diff 里只有分数变化，reviewer 一眼看到「0.87 → 0.91」。存了时间戳就每次都是噪声 diff。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .report import SuiteReport

_DIR = Path(__file__).parent / "baselines"


def path_for(suite: str) -> Path:
    return _DIR / f"{suite}.json"


def load(suite: str) -> dict | None:
    """读基线；不存在返回 None（首次跑还没基线，属正常）。"""
    p = path_for(suite)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save(report: SuiteReport, suite: str | None = None) -> Path:
    """把本轮 metrics 固化成基线。只留 mean/n —— 见模块头。"""
    suite = suite or report.suite
    data = {"suite": suite,
            "metrics": {k: {"mean": v["mean"], "n": v["n"]}
                        for k, v in sorted(report.metrics().items())}}
    p = path_for(suite)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


@dataclass
class Diff:
    metric: str
    baseline: float
    current: float
    delta: float
    regressed: bool


def compare(report: SuiteReport, baseline: dict | None, *, tolerance: float = 0.0) -> list[Diff]:
    """逐指标比对。基线里有、本轮没有的指标记为跌到 0（打分器被删/改名也该让门禁红）。"""
    if not baseline:
        return []
    cur = report.metrics()
    out: list[Diff] = []
    for name, b in baseline.get("metrics", {}).items():
        bm = float(b["mean"])
        cm = float(cur[name]["mean"]) if name in cur else 0.0
        delta = cm - bm
        out.append(Diff(name, bm, cm, round(delta, 6), delta < -tolerance))
    return out


def gate(report: SuiteReport, diffs: list[Diff], *,
         max_error_rate: float = 0.1) -> tuple[bool, str]:
    """(通过?, 人读原因)。

    「没跑成」与「不及格」是两回事，分开报：error_rate 超阈值说明这轮结果无效
    （端点/环境问题），不该被当成质量结论。
    """
    er = report.error_rate()
    if er > max_error_rate:
        return False, (f"本轮 {er:.0%} 的打分跑挂了（阈值 {max_error_rate:.0%}）——"
                       f"这是「没跑成」不是「不及格」，先查环境/端点再看分数")
    bad = [d for d in diffs if d.regressed]
    if bad:
        detail = "；".join(f"{d.metric} {d.baseline:.3f} → {d.current:.3f}（{d.delta:+.3f}）"
                           for d in bad)
        return False, f"{len(bad)} 个指标低于基线：{detail}"
    if not diffs:
        return True, "无基线可比（首次跑？跑 update-baseline 固化）"
    improved = [d for d in diffs if d.delta > 0]
    if improved:
        detail = "；".join(f"{d.metric} {d.baseline:.3f} → {d.current:.3f}" for d in improved)
        return True, f"全部不低于基线；其中 {len(improved)} 个变好：{detail}（确认后跑 update-baseline）"
    return True, f"全部与基线持平（{len(diffs)} 个指标）"

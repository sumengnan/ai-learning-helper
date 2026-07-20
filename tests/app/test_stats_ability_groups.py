# tests/app/test_stats_ability_groups.py
"""「AI 在为我做什么」能力分组的防腐化护栏。

这张表把原始工具名映射成面向学习者的能力标签。它会随工具集演进自然腐化，而且腐化时
**不报错**——所有调用悄悄堆进「其他能力」，产品叙事失效，没有任何测试会红。

实际发生过：表里只有内置工具，全部应用层工具（知识库/题库/考试）与全部 MCP 远程工具
都没进表；而 EXECUTOR_GUIDE 恰恰引导模型优先用 MCP 搜索工具，「联网查资料」因此注定
接近 0。
"""
import pytest

from app.stats import _ABILITY_GROUPS, _ability_label


def _table_names() -> set[str]:
    names: set[str] = set()
    for _icon, _label, group in _ABILITY_GROUPS:
        names |= group
    return names


def test_no_duplicate_names_across_groups():
    """同一个工具落进两个组会被重复计数，总和大于实际调用次数。"""
    seen: set[str] = set()
    for _icon, _label, group in _ABILITY_GROUPS:
        dup = seen & group
        assert not dup, f"工具名重复归组：{dup}"
        seen |= group


def test_labels_are_unique():
    """标签重复会在结果里出现两行同名条目。"""
    labels = [label for _icon, label, _g in _ABILITY_GROUPS]
    assert len(labels) == len(set(labels)), f"标签重复：{labels}"


@pytest.mark.parametrize("name,want", [
    # MCP 远程工具：名字由外部服务器决定，只能按关键词归类
    ("mcp__websearch__bailian_web_search", "联网查资料"),
    ("mcp__tavily__search", "联网查资料"),
    ("mcp__x__fetch_page", "联网查资料"),
    # 应用层工具：曾整批漏在表外
    ("save_to_knowledge", "整理进知识库"),
    ("start_exam", "考试与错题"),
    ("generate_questions", "出练习题"),
    ("calculator", "做计算"),
    # 内置工具
    ("http_request", "联网查资料"),
    ("search_knowledge", "检索知识库"),
    ("search_memory", "记住你的偏好"),
])
def test_known_tools_are_classified(name, want):
    assert _ability_label(name) == want


def test_unknown_mcp_tool_falls_back_honestly():
    """归不了类就归「其他能力」——好过硬塞进某一类造成假象。"""
    assert _ability_label("mcp__weather__get_forecast") is None
    assert _ability_label("some_future_tool") is None


def test_every_table_name_is_a_real_tool():
    """表里的名字必须是真实存在的工具类名，防止写错字或留下已删工具的死条目。

    从源码里扫 `name = "..."` 收集全部工具名——比起真去装配一个 harness（要 api_key、
    要建库），这样既快又不依赖环境。
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    declared: set[str] = set()
    for d in ("app/tools", "src/harness/tools", "src/harness/skills",
              "src/harness/orchestration"):
        for f in (root / d).rglob("*.py"):
            declared |= set(re.findall(r'^\s{4}name = "([^"]+)"', f.read_text(encoding="utf-8"),
                                       re.M))
    missing = _table_names() - declared
    assert not missing, f"分组表里的工具名在代码里不存在（写错或已删）：{sorted(missing)}"


# ---------- 延迟统计不得收编造的 0 ----------

def test_zero_latency_is_excluded_from_percentiles():
    """embedding / rerank 上报的 ModelUsage 把 latency_ms 硬编码成 0.0
    （memory/embeddings.py、memory/reranker.py）。收进来会把均值与 p95 系统性拉低——
    检索用得越多显得越快，与直觉相反。只统计真实测过的耗时。

    这是源码级护栏：真正的行为测试要搭 trajectory 库、造事件，成本远高于它能带来的
    信心；而这里要防的是「有人把 lat > 0 改回 isinstance 判断」这种具体回归。
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[2] / "app" / "stats.py").read_text(
        encoding="utf-8")
    i = src.index('lat = d.get("latency_ms")')
    window = src[i:i + 200]
    assert "lat > 0" in window, "latency_ms 必须过滤掉 0，否则被硬编码 0 的上报稀释"

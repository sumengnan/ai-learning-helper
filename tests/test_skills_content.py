# tests/test_skills_content.py
"""对随仓库发布的技能剧本（skills/*/SKILL.md）做内容层面的守规检查。

技能是 Markdown，没有编译期检查，写错了只能等线上表现暴露。而技能剧本是**最贴近任务
的指令**，与系统提示词冲突时它会赢——study-plan 曾明写「导出计划表（… + 配套题 id）」，
直接抵消了 EXAM_GUIDE 和工具结果里「别外露 id」的两处约束。故在此钉死几条底线。
"""
from pathlib import Path

import pytest

_SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


def _skill_files() -> list[Path]:
    return sorted(_SKILLS_DIR.glob("*/SKILL.md"))


def test_skills_dir_is_present():
    """防呆：路径写错会让下面的参数化测试静默零用例，看着全绿其实什么都没测。"""
    assert _skill_files(), f"未找到任何 SKILL.md（查找路径：{_SKILLS_DIR}）"


@pytest.mark.parametrize("path", _skill_files(), ids=lambda p: p.parent.name)
def test_skill_does_not_write_question_ids_into_user_output(path: Path):
    """技能不得指示把题目 id 写进交付给用户的内容（计划表/报告/回答）。

    区分正当用法：为 start_exam(source="ids") 在上下文里「记住 id」是必要的，不算违规；
    违规的是让 id 出现在用户看得见的产物里。
    """
    text = path.read_text(encoding="utf-8")
    # 「导出/产出某文档（… 题 id）」这类把 id 列进交付物清单的写法
    banned = ("+ 配套题 id", "+ 题 id", "题目 id）", "题 id）")
    hits = [b for b in banned if b in text]
    assert not hits, f"{path.parent.name} 指示把题目 id 写进用户可见产物：{hits}"


def test_study_plan_exports_stems_not_ids():
    """study-plan 是该问题的原发地：导出步骤必须落在题干上，并显式禁止 id。"""
    text = (_SKILLS_DIR / "study-plan" / "SKILL.md").read_text(encoding="utf-8")
    assert "配套练习题的**题干**" in text, "导出计划表应配题干"
    assert "题目 id 绝不写进计划表或回答" in text, "缺少显式禁令"


# ---------- 真实技能的路由：拿仓库里的 skills/ 跑，不用临时假数据 ----------

_ROUTING_CASES = [
    # 用户实际说过的话 → 应命中的技能
    ("搜索最新的 AI 资讯，保存到知识库", "research-learning"),
    ("查一下最新进展", "research-learning"),
    ("帮我搜集最新资讯", "research-learning"),
    ("整理这份资料，据此出题", "material-to-knowledge"),
    ("把这份资料消化一下", "material-to-knowledge"),
    ("帮我制定 7 天的 AI 学习计划", "study-plan"),
    ("讲讲我的错题", "wrong-answer-remediation"),
    ("什么是注意力机制", "concept-teaching"),
    ("模考一次", "exam-prep"),
    ("今天复习什么", "spaced-review"),
    ("讲讲这段代码", "code-learning"),
]


@pytest.mark.parametrize("msg,want", _ROUTING_CASES, ids=[c[1] + "|" + c[0][:10]
                                                          for c in _ROUTING_CASES])
def test_real_skills_route_as_expected(msg, want):
    """用仓库里真实的 skills/ 做路由回归。

    触发词是纯子串匹配，改一个词就可能把别的技能的流量抢走——而这类回归在单元测试里
    用临时假技能是测不出来的。这张表就是「哪句话该走哪个技能」的事实基准。
    """
    from harness.skills.matcher import SkillMatcher
    from harness.skills.registry import SkillRegistry

    m = SkillMatcher(SkillRegistry(str(_SKILLS_DIR)))
    got = m.match(msg)
    assert got is not None and got.name == want, \
        f"「{msg}」命中 {got.name if got else '（无）'}，期望 {want}"

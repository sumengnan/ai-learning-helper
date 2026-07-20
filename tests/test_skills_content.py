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

# tests/test_skills_content.py
"""对随仓库发布的技能剧本（skills/*/SKILL.md）做内容层面的守规检查。

技能是 Markdown，没有编译期检查，写错了只能等线上表现暴露。而技能剧本是**最贴近任务
的指令**，与系统提示词冲突时它会赢——study-plan 曾明写「导出计划表（… + 配套题 id）」，
直接抵消了 EXAM_GUIDE 和工具结果里「别外露 id」的两处约束。故在此钉死几条底线。

（study-plan 本身已随 4 个技能一并删除，针对它的专项用例也随之移除；下面的参数化守规
对现存及日后新增的技能继续生效。）
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


@pytest.mark.parametrize("path", _skill_files(), ids=lambda p: p.parent.name)
def test_skill_does_not_instruct_asking_the_user(path: Path):
    """技能不得指示「问用户」——技能是规划器的拆解蓝本，这类措辞会被抄进步骤描述。

    计划在一轮内自主跑完，执行子步没有与用户对话的通道，这种步骤会被 validate_plan
    直接打回（见 plan._ASK_USER_RE），白费一轮重试；漏过去也只会输出「需要用户提供
    X」，拖成「信息不足，无法完成」。技能要教的是「按合理默认推进 + 说明假设」。
    """
    from app.orchestration.plan import _ASK_USER_RE
    hits = [(i, m.group(0))
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if (m := _ASK_USER_RE.search(line))]
    assert not hits, f"{path.parent.name} 含「问用户」措辞（行号, 命中）：{hits}"

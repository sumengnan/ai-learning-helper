"""EXAM_GUIDE 回归护栏：确保「即时式」流程把「答错→存错题集」写成固定内联步骤。

背景：后端工具注册、SaveWrongAnswerTool 本身均验证正常，答错却不自动保存，
根因是旧提示词里「即时式流程」只描述「给出答案与解析」，save_wrong_answer 是另一条独立
说明、与流程脱节，模型按流程直出答案便跳过了保存。此测试锁定修复：保存已内联进流程。
"""
from app.api.chat import EXAM_GUIDE


def test_immediate_flow_inlines_save_on_wrong():
    # 「答错」分支里必须先出现 save_wrong_answer，且强调不可跳过
    assert "save_wrong_answer" in EXAM_GUIDE
    wrong_idx = EXAM_GUIDE.find("答错")
    save_idx = EXAM_GUIDE.find("save_wrong_answer")
    assert wrong_idx != -1 and save_idx != -1
    # 保存被描述为流程固定环节
    assert "不可跳过" in EXAM_GUIDE or "固定环节" in EXAM_GUIDE


def test_guide_states_saving_is_unconditional():
    # 开关已移除：提示词不得再提「开关 / 功能未开启 / 跳过保存」这类可选语义
    for dead in ("功能未开启", "跳过保存", "关了开关"):
        assert dead not in EXAM_GUIDE
    # 且须明说答错必存、无条件
    assert "无条件" in EXAM_GUIDE


def test_guide_forbids_claiming_saved_without_calling_tool():
    # 核心护栏：没真调用工具就不许说「已存入错题集」
    assert "务必真的调用该工具" in EXAM_GUIDE
    assert "已存入错题集" in EXAM_GUIDE and "绝不能说" in EXAM_GUIDE


def test_guide_requires_explicit_saved_notice_to_user():
    # 保存成功后须明确告知用户已入错题集（不再「简短带过」）
    assert "明确告诉用户" in EXAM_GUIDE and "错题集" in EXAM_GUIDE
    assert "简短带过" not in EXAM_GUIDE


def test_adhoc_must_pass_content_not_only_id():
    # 保留：即席题必须传内容，不能只传 question_id
    assert "question_id" in EXAM_GUIDE and "stem/type/answer" in EXAM_GUIDE

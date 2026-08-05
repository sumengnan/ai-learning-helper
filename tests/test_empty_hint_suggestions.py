from pathlib import Path

import pytest

_EMPTY_HINT = Path(__file__).resolve().parents[1] / "web" / "src" / "components" / "EmptyHint.tsx"


def test_suggestions_contains_generate_5_ai_questions():
    """SUGGESTIONS 数组必须包含「生成5道ai题」这条默认对话。"""
    text = _EMPTY_HINT.read_text(encoding="utf-8")
    assert "生成5道ai题" in text, (
        "SUGGESTIONS 中缺少「生成5道ai题」这条默认对话，请添加到 EmptyHint.tsx 的 SUGGESTIONS 数组"
    )

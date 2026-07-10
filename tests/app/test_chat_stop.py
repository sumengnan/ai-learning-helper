from app.api.chat import _final_content


def test_final_content_prefers_completed_final():
    assert _final_content("完整答案", "部分") == "完整答案"


def test_final_content_keeps_partial_when_stopped():
    # 无 final（中途停止）：保留已生成的部分文本并标注已停止
    assert _final_content(None, "已经写了一半") == "已经写了一半\n\n（已停止）"


def test_final_content_placeholder_when_nothing_generated():
    assert _final_content(None, "") == "（本轮已停止）"
    assert _final_content(None, "   ") == "（本轮已停止）"

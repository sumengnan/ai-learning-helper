"""EXAM_GUIDE 条件注入：命中考试语境才注入，且绝不在多轮练习中途掉链子。

风险不对称——漏注入会让「答错必存」等保证静默失效（高代价），多注入只浪费约 0.3%
窗口（低代价），故检测器刻意偏向注入。这些测试锁定「不能漏」的各条路径。
"""
from app.api.chat import _needs_exam_guide
from harness.types import Message, Role, ToolCall


def _u(text):
    return Message(role=Role.USER, content=text)


def _tool(name):
    return Message(role=Role.ASSISTANT, tool_calls=[ToolCall(id="c", name=name, arguments={})])


# ---------- 不该注入：与考试无关的绝大多数轮次 ----------

def test_plain_coding_task_no_guide():
    assert _needs_exam_guide("生成一段 Python 代码执行", [], False) is False


def test_plain_qa_no_guide():
    assert _needs_exam_guide("什么是 JVM 的逃逸分析", [_u("你好"), _u("讲讲多线程")], False) is False


def test_generate_to_bank_is_not_exam():
    # 出题入库不是考试：误判成考试会让裁判按「讲解上一题+呈现当前题」判失败（实测 bug）
    for msg in ("生成 5 道 AI 相关的单选题，保存到题库", "出10道题存进题库",
                "生成一批 Python 题目入库", "帮我出几道题收录到题库"):
        assert _needs_exam_guide(msg, [], False) is False, msg


def test_generate_then_quiz_is_still_exam():
    # 出题+要考我：仍是考试（有明确答题意图，不豁免）
    for msg in ("出5道题考我", "生成几道题然后测验我", "存到题库并考考我"):
        assert _needs_exam_guide(msg, [], False) is True, msg


# ---------- 该注入：三个信号各自 ----------

def test_trigger_word_in_current_message():
    for msg in ("考我几道题", "来道题练练", "我想刷题", "复习一下错题", "帮我出题", "开考"):
        assert _needs_exam_guide(msg, [], False) is True, msg


def test_active_exam_session_even_without_trigger():
    # 考试进行中，用户此刻只回一个选项，无任何触发词 —— 必须注入
    assert _needs_exam_guide("A", [], exam_active=True) is True


def test_history_tool_call_keeps_guide_when_answering():
    # 模型上一轮调 sample_questions 出题，本轮用户答「B」（无触发词、无 session）
    history = [_u("刷题"), _tool("sample_questions"),
               Message(role=Role.ASSISTANT, content="第1题：……")]
    assert _needs_exam_guide("B", history, False) is True


def test_batch_practice_midway_not_dropped():
    """批量抽题后逐题问答：sample_questions 只调一次，中间轮既无触发词也无 session。
    只要那次调用还在窗口内，指引就不能掉 —— 这是最容易漏的一条。"""
    history = [_u("给我来5道题"), _tool("sample_questions")]
    # 模拟已经问答了两题（每题约 2 条消息），现在答第 3 题
    for i in range(2):
        history += [Message(role=Role.ASSISTANT, content=f"第{i+1}题…（讲评）"), _u(f"答案{i}")]
    assert _needs_exam_guide("我选 C", history, False) is True


def test_history_user_trigger_within_window():
    # 用户几轮前说过「考试」，其后是普通往返，仍在窗口内 → 保持注入
    history = [_u("我们来做个测验吧"),
               Message(role=Role.ASSISTANT, content="好，第1题…"), _u("A")]
    assert _needs_exam_guide("继续", history, False) is True


# ---------- 边界：语境早已结束则退出 ----------

def test_guide_drops_after_exam_context_scrolls_out():
    """考试活动滚出窗口后，后续无关对话不再注入 —— 否则等于没省。"""
    history = [_u("考我"), _tool("sample_questions")]
    # 之后堆足够多的无关往返，把考试活动挤出 16 条窗口
    for i in range(12):
        history += [_u(f"聊聊别的{i}"), Message(role=Role.ASSISTANT, content=f"回复{i}")]
    assert _needs_exam_guide("再讲讲设计模式", history, False) is False


def test_multimodal_content_does_not_crash():
    # 历史里的多模态消息（content 为 list）不能让检测器炸
    history = [Message(role=Role.USER, content=[{"type": "text", "text": "考我"}])]
    # list 型 content 不做触发词匹配（只匹配 str），这里应回退为 False 而非抛错
    assert _needs_exam_guide("写段代码", history, False) is False


# ---- _in_stateful_exam：比 _needs_exam_guide 窄一档，专用于「是否关掉技能路由」 ----

def _tool_msg(name):
    return Message(role=Role.ASSISTANT, tool_calls=[ToolCall(id="c", name=name, arguments={})])


def test_stateful_exam_false_for_mere_trigger_word():
    """只是嘴上提到「错题」不算身处考试——否则错题精讲技能会被自己的触发词挡住。

    覆盖 Bug：「讲讲我的错题」命中考试触发词后技能路由被跳过，实测只有「我哪里薄弱」
    这类不含考试词的说法才命中得了 wrong-answer-remediation。
    """
    from app.api.chat import _in_stateful_exam, _needs_exam_guide
    assert _needs_exam_guide("讲讲我的错题", [], False) is True    # 指引照常注入（宽）
    assert _in_stateful_exam([], False) is False                  # 但不算有状态（窄）


def test_stateful_exam_true_while_exam_active():
    """考试会话进行中 → 有状态：此刻用户在逐题作答，技能剧本会打乱推进。"""
    from app.api.chat import _in_stateful_exam
    assert _in_stateful_exam([], True) is True


def test_stateful_exam_true_after_exam_tool_called():
    """近期调过考试工具 → 有状态：覆盖模型自驱的多轮练习（中间轮无触发词、无 session）。"""
    from app.api.chat import _in_stateful_exam
    assert _in_stateful_exam([_tool_msg("start_exam")], False) is True
    assert _in_stateful_exam([_tool_msg("sample_questions")], False) is True


def test_stateful_exam_ignores_unrelated_tools():
    """非考试工具不算数，否则任何用过工具的会话都会被当成考试中。"""
    from app.api.chat import _in_stateful_exam
    assert _in_stateful_exam([_tool_msg("save_download"), _tool_msg("browse")], False) is False

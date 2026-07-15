from app.context import ConversationContextManager, LayeredContextManager
from app.context_assembly import ContextAssembler
from harness.state import RunState
from harness.types import Message, Role
from harness.usage import count_message_tokens


class _Cfg:
    context_strategy = "full"
    context_window_tokens = 128000
    context_response_reserve_tokens = 4096
    context_working_ratio = 0.5
    context_summary_max_tokens = 2000
    context_retrieval_top_k = 5
    context_enable_summary = True
    context_enable_retrieval = True


def _turn(u, a):
    return [Message(role=Role.USER, content=u), Message(role=Role.ASSISTANT, content=a)]


def _state():
    st = RunState(run_id="r1")
    st.append(Message(role=Role.USER, content="本轮问题"))
    return st


# ---------- LayeredContextManager 纯拼装 ----------

def test_layered_build_order_and_optional_blocks():
    kept = _turn("最近问", "最近答")
    m = LayeredContextManager(
        "系统",
        Message(role=Role.SYSTEM, content="摘要块"),
        Message(role=Role.USER, content="召回块"),
        kept)
    built = m.build(_state())
    assert built[0].content == "系统"
    assert built[1].content == "摘要块"
    assert built[2].content == "召回块"
    assert built[3:5] == kept
    assert built[-1].content == "本轮问题"


def test_layered_build_omits_none_blocks():
    m = LayeredContextManager("系统", None, None, _turn("q", "a"))
    built = m.build(_state())
    assert [b.role for b in built[:1]] == [Role.SYSTEM]
    assert all(b.content not in ("摘要块", "召回块") for b in built)


# ---------- ContextAssembler 策略切换 ----------

async def test_full_strategy_equivalent_to_conversation_manager():
    cfg = _Cfg()
    cfg.context_strategy = "full"
    hist = _turn("a", "b") + _turn("c", "d")
    asm = ContextAssembler(cfg, "gpt-4o-mini")
    mgr = await asm.build_manager("系统", hist, "q", "c1")
    assert isinstance(mgr, ConversationContextManager)
    expected = ConversationContextManager("系统", hist).build(_state())
    assert [(m.role, m.content) for m in mgr.build(_state())] == \
           [(m.role, m.content) for m in expected]


async def test_window_strategy_drops_old_turns_no_summary():
    cfg = _Cfg()
    cfg.context_strategy = "window"
    hist = _turn("轮1", "答1") + _turn("轮2", "答2") + _turn("轮3", "答3")
    # 逼近极小预算：只留最后一轮
    cfg.context_window_tokens = count_message_tokens(hist[4:], "gpt-4o-mini") + 4100
    cfg.context_response_reserve_tokens = 4096
    cfg.context_working_ratio = 1.0
    asm = ContextAssembler(cfg, "gpt-4o-mini")
    mgr = await asm.build_manager("s", hist, "q", "c1")
    built = mgr.build(RunState(run_id="r"))
    contents = [m.content for m in built]
    assert "轮1" not in contents and "轮3" in contents      # 丢老留新


class _FakeSummarizer:
    async def ensure(self, conv_id, evicted):
        return "这是更早对话的摘要"


class _FakeConvMemory:
    async def retrieve(self, conv_id, query, k, *, before_seq=None):
        class H:
            text = "更早的相关片段"
        return [H()]


async def test_layered_strategy_injects_summary_and_retrieval():
    cfg = _Cfg()
    cfg.context_strategy = "layered"
    cfg.context_working_ratio = 0.0        # 强制所有历史被挤出 → 有 evicted
    hist = _turn("轮1", "答1") + _turn("轮2", "答2")
    asm = ContextAssembler(cfg, "gpt-4o-mini",
                           summarizer=_FakeSummarizer(), conv_memory=_FakeConvMemory())
    mgr = await asm.build_manager("s", hist, "现在的问题", "c1")
    contents = [m.content for m in mgr.build(RunState(run_id="r"))]
    assert any("这是更早对话的摘要" in c for c in contents)
    assert any("更早的相关片段" in c for c in contents)


async def test_window_bounds_long_history_and_no_orphan_tools():
    """核心价值：超窗口的长会话（含工具轮）被裁到预算内，且不留孤儿 tool 消息。"""
    from harness.types import ToolCall

    def _tool_turn(i):
        return [
            Message(role=Role.USER, content=f"第{i}轮：算点东西 " + "填充" * 20),
            Message(role=Role.ASSISTANT, content=None,
                    tool_calls=[ToolCall(id=f"c{i}", name="run_python",
                                         arguments={"code": f"print({i})"})]),
            Message(role=Role.TOOL, content=str(i), tool_call_id=f"c{i}"),
            Message(role=Role.ASSISTANT, content=f"第{i}轮结果 " + "填充" * 20),
        ]

    hist = [m for i in range(30) for m in _tool_turn(i)]   # 长历史
    cfg = _Cfg()
    cfg.context_strategy = "window"
    cfg.context_window_tokens = 6000        # 小窗口逼裁剪
    cfg.context_response_reserve_tokens = 1000
    cfg.context_working_ratio = 0.5
    asm = ContextAssembler(cfg, "gpt-4o-mini")
    mgr = await asm.build_manager("你是助手", hist, "继续", "c1")
    built = mgr.build(RunState(run_id="r"))

    # ① 输出规模受控（远小于全量 30 轮）
    assert count_message_tokens(built, "gpt-4o-mini") <= cfg.context_window_tokens
    assert len(built) < len(hist)
    # ② 无孤儿 tool：每个 tool 结果都有对应的 tool_call
    tool_ids = {m.tool_call_id for m in built if m.role == Role.TOOL}
    call_ids = {tc.id for m in built for tc in m.tool_calls}
    assert tool_ids <= call_ids


async def test_layered_preserves_tool_pairing_with_blocks():
    """layered（前置摘要+召回块）裁剪含工具轮的长历史，仍不破坏「tool_calls ↔ tool 结果」
    配对、不留孤儿 tool——这是多轮工具任务（考试回放已抽题目）在 layered 下的正确性前提。"""
    from harness.types import ToolCall

    def _tool_turn(i):
        return [
            Message(role=Role.USER, content=f"第{i}轮：抽题 " + "填充" * 20),
            Message(role=Role.ASSISTANT, content=None,
                    tool_calls=[ToolCall(id=f"c{i}", name="sample_questions",
                                         arguments={"count": 10})]),
            Message(role=Role.TOOL, content=f"[第{i}轮的10题]", tool_call_id=f"c{i}"),
            Message(role=Role.ASSISTANT, content=f"第{i}轮：第1题"),
        ]

    hist = [m for i in range(30) for m in _tool_turn(i)]   # 超窗口长历史
    cfg = _Cfg()
    cfg.context_strategy = "layered"
    cfg.context_window_tokens = 6000
    cfg.context_response_reserve_tokens = 1000
    cfg.context_working_ratio = 0.5
    asm = ContextAssembler(cfg, "gpt-4o-mini",
                           summarizer=_FakeSummarizer(), conv_memory=_FakeConvMemory())
    mgr = await asm.build_manager("你是助手", hist, "继续考试", "c1")
    built = mgr.build(RunState(run_id="r"))

    # ① 每个 tool 结果都出现在其 tool_call 之后（配对完整、顺序正确，绝不孤儿）
    open_ids: set[str] = set()
    for m in built:
        if m.role == Role.TOOL:
            assert m.tool_call_id in open_ids, "layered 裁剪产生了孤儿 tool 结果"
        if m.role == Role.ASSISTANT and m.tool_calls:
            open_ids.update(tc.id for tc in m.tool_calls)
    # ② 规模仍受窗口约束
    assert count_message_tokens(built, "gpt-4o-mini") <= cfg.context_window_tokens


async def test_layered_summary_failure_degrades_gracefully():
    cfg = _Cfg()
    cfg.context_strategy = "layered"
    cfg.context_working_ratio = 0.0
    cfg.context_enable_retrieval = False

    class _Boom:
        async def ensure(self, conv_id, evicted):
            raise RuntimeError("LLM 挂了")

    hist = _turn("轮1", "答1")
    asm = ContextAssembler(cfg, "gpt-4o-mini", summarizer=_Boom())
    mgr = await asm.build_manager("s", hist, "q", "c1")   # 不抛异常
    built = mgr.build(RunState(run_id="r"))
    assert built[0].role == Role.SYSTEM


# ---- 输入总量的策略上限（分档计价场景）----

class _CapCfg(_Cfg):
    """窗口很大（塞得下），但用 max_prompt 把输入压在档位阈值内（超档贵数倍）。"""
    context_strategy = "window"          # 只跑 L1，隔离掉摘要/检索的干扰
    context_window_tokens = 1000000
    context_response_reserve_tokens = 64000
    context_working_ratio = 0.9
    context_max_prompt_tokens = 0        # 由各用例覆盖


async def test_max_prompt_tokens_evicts_more_than_physical_window_would():
    """同样的历史：不设上限时全留；设了上限就该开始淘汰 —— 证明它真的透传到了窗口切割。"""
    history = []
    for i in range(60):
        history += _turn(f"问题{i} " * 60, f"回答{i} " * 60)

    class _NoCap(_CapCfg):
        context_max_prompt_tokens = 0

    class _Cap(_CapCfg):
        context_max_prompt_tokens = 3000

    m_nocap = await ContextAssembler(_NoCap(), "gpt-4o-mini").build_manager(
        "系统", history, "q", "c1")
    m_cap = await ContextAssembler(_Cap(), "gpt-4o-mini").build_manager(
        "系统", history, "q", "c1")
    kept_nocap = m_nocap.build(RunState(run_id="r", messages=[]))
    kept_cap = m_cap.build(RunState(run_id="r", messages=[]))
    # 物理窗口 100 万，这点历史绰绰有余 → 不设上限时一条不淘汰
    assert len(kept_nocap) == len(history) + 1          # +1 是 system
    # 设了 3000 的输入上限 → 必须开始淘汰
    assert len(kept_cap) < len(kept_nocap)
    assert count_message_tokens(kept_cap, "gpt-4o-mini") <= 3000


async def test_no_cap_by_default_keeps_previous_behavior():
    """不配 context_max_prompt_tokens（存量部署）→ 行为与加该字段前一致。"""
    history = _turn("你好", "你好呀")
    m = await ContextAssembler(_Cfg(), "gpt-4o-mini").build_manager("系统", history, "q", "c1")
    assert isinstance(m, ConversationContextManager)    # full 策略原样直通

from app.context import ConversationContextManager
from harness.state import RunState
from harness.types import Message, Role


def test_build_injects_history_in_order():
    hist = [Message(role=Role.USER, content="旧问"), Message(role=Role.ASSISTANT, content="旧答")]
    ctx = ConversationContextManager("你是助手", hist)
    st = RunState(run_id="r1"); st.append(Message(role=Role.USER, content="新问"))
    built = ctx.build(st)
    assert built[0].role == Role.SYSTEM
    assert [m.content for m in built] == ["你是助手", "旧问", "旧答", "新问"]

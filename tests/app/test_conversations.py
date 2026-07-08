from app.conversations import ConversationStore
from harness.types import Message, Role


def test_create_list_delete():
    s = ConversationStore(":memory:")
    cid = s.create("测试")
    assert any(c["id"] == cid and c["title"] == "测试" for c in s.list())
    assert s.exists(cid) is True
    s.delete(cid)
    assert s.exists(cid) is False


def test_append_and_messages_roundtrip():
    s = ConversationStore(":memory:")
    cid = s.create()
    s.append(cid, [Message(role=Role.USER, content="hi"),
                   Message(role=Role.ASSISTANT, content="yo")])
    s.append(cid, [Message(role=Role.USER, content="再问")])
    msgs = s.messages(cid)
    assert [m.content for m in msgs] == ["hi", "yo", "再问"]
    assert msgs[0].role == Role.USER and msgs[1].role == Role.ASSISTANT

from app.conversations import ConversationStore
from harness.types import Message, Role


def test_create_list_delete():
    s = ConversationStore(":memory:")
    cid = s.create("u1", "测试")
    assert any(c["id"] == cid and c["title"] == "测试" for c in s.list("u1"))
    assert s.exists("u1", cid) is True
    s.delete("u1", cid)
    assert s.exists("u1", cid) is False


def test_append_and_messages_roundtrip():
    s = ConversationStore(":memory:")
    cid = s.create("u1")
    s.append(cid, [Message(role=Role.USER, content="hi"),
                   Message(role=Role.ASSISTANT, content="yo")])
    s.append(cid, [Message(role=Role.USER, content="再问")])
    msgs = s.messages(cid)
    assert [m.content for m in msgs] == ["hi", "yo", "再问"]
    assert msgs[0].role == Role.USER and msgs[1].role == Role.ASSISTANT


def test_add_run_and_run_ids_scoped_by_owner():
    s = ConversationStore(":memory:")
    cid = s.create("u1")
    s.add_run(cid, "run-a")
    s.add_run(cid, "run-b")
    s.add_run(cid, "run-a")                      # 重复登记幂等
    assert sorted(s.run_ids("u1", cid)) == ["run-a", "run-b"]
    # 归属校验：非本人 / 不存在的会话都返回空
    assert s.run_ids("u2", cid) == []
    assert s.run_ids("u1", "nope") == []


def test_delete_clears_runs_but_not_other_conversations():
    s = ConversationStore(":memory:")
    c1 = s.create("u1")
    c2 = s.create("u1")
    s.add_run(c1, "r1")
    s.add_run(c2, "r2")
    s.delete("u1", c1)
    assert s.run_ids("u1", c1) == []             # 该会话的运行映射被清
    assert s.run_ids("u1", c2) == ["r2"]         # 其他会话不受影响


def test_append_steps_persist_for_ui_only():
    s = ConversationStore(":memory:")
    cid = s.create("u1")
    steps = [{"tool": "calculator", "args": {"expression": "1+1"},
              "result": "2", "is_error": False}]
    s.append(cid, [Message(role=Role.USER, content="1+1?"),
                   Message(role=Role.ASSISTANT, content="是 2")], steps=steps)
    ui = s.ui_messages(cid)
    assert [m["role"] for m in ui] == ["user", "assistant"]
    assert ui[0]["steps"] is None          # 用户消息不带 steps
    assert ui[1]["steps"] == steps         # 工具轨迹挂在助手消息上，切换回来可还原
    # LLM 历史不受影响（steps 纯 UI 用途）
    assert [m.content for m in s.messages(cid)] == ["1+1?", "是 2"]

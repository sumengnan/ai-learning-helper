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


def test_messages_replays_tool_calls_into_llm_history():
    """带工具轨迹(steps)的助手轮：messages() 回放「工具调用+结果」供下一轮模型看到，
    使多轮工具任务（如考试抽题）不因上下文丢工具产出而重复调用。"""
    s = ConversationStore(":memory:")
    cid = s.create("u1", "考试")
    s.start_turn(cid, Message(role=Role.USER, content="从题库抽10题考试"), "r1")
    s.finish_turn(cid, "r1", "第1题：1+1=?",
                  steps=[{"tool": "sample_questions", "args": {"count": 10},
                          "result": "[题1…题10 的完整JSON]", "is_error": False}])
    msgs = s.messages(cid)
    assert [m.role.value for m in msgs] == ["user", "assistant", "tool", "assistant"]
    assert msgs[1].tool_calls[0].name == "sample_questions"
    assert msgs[1].tool_calls[0].arguments == {"count": 10}
    assert "题10" in msgs[2].content                       # 抽到的题回放进上下文
    assert msgs[2].tool_call_id == msgs[1].tool_calls[0].id  # 调用与结果配对
    assert msgs[3].content == "第1题：1+1=?"


def test_messages_multi_step_replayed_in_order():
    s = ConversationStore(":memory:")
    cid = s.create("u1")
    s.start_turn(cid, Message(role=Role.USER, content="做题"), "r1")
    s.finish_turn(cid, "r1", "结果",
                  steps=[{"tool": "a", "args": {}, "result": "ra"},
                         {"tool": "b", "args": {}, "result": "rb"}])
    roles = [m.role.value for m in s.messages(cid)]
    assert roles == ["user", "assistant", "tool", "assistant", "tool", "assistant"]


def test_messages_without_steps_unchanged():
    s = ConversationStore(":memory:")
    cid = s.create("u1")
    s.start_turn(cid, Message(role=Role.USER, content="hi"), "r1")
    s.finish_turn(cid, "r1", "yo")
    assert [m.content for m in s.messages(cid)] == ["hi", "yo"]


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


def test_finish_turn_persists_usage_and_elapsed():
    s = ConversationStore(":memory:")
    cid = s.create("u1", "计时")
    s.start_turn(cid, Message(role=Role.USER, content="问题"), run_id="R1")
    s.finish_turn(cid, "R1", "答案", status="done",
                  tokens=1234, cost=0.02, elapsed_ms=65000)
    ui = s.ui_messages(cid)
    asst = [m for m in ui if m["role"] == "assistant"][-1]
    assert asst["tokens"] == 1234
    assert asst["cost"] == 0.02
    assert asst["elapsed_ms"] == 65000


def test_finish_turn_defaults_meta_to_none():
    s = ConversationStore(":memory:")
    cid = s.create("u1", "无用量")
    s.start_turn(cid, Message(role=Role.USER, content="q"), run_id="R2")
    s.finish_turn(cid, "R2", "a", status="done")   # 不传用量/耗时
    asst = [m for m in s.ui_messages(cid) if m["role"] == "assistant"][-1]
    assert asst["tokens"] is None and asst["cost"] is None and asst["elapsed_ms"] is None


def test_append_steps_persist_for_ui_and_replay_to_llm():
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
    # LLM 历史：工具调用+结果被回放（供下一轮模型看到本轮工具产出）
    llm = s.messages(cid)
    assert [m.role.value for m in llm] == ["user", "assistant", "tool", "assistant"]
    assert llm[1].tool_calls[0].name == "calculator"
    assert llm[2].content == "2"
    assert llm[3].content == "是 2"

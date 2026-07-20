"""agent 评测管线的自测：MockModelClient 驱动 AgentDriver + 脚本化 judge，零网络。

测的**不是 agent 质量**（那需要真模型，见 evals/datasets/agent.jsonl，只手动跑），
而是 runner/driver/scorer 这条管线本身没坏。有它才能在真实层出问题时区分
「模型退化」和「eval 代码退化」—— 否则真实层一红，你不知道该怀疑谁。
"""
import pytest

from evals.drivers import AgentDriver, scripted_complete
from evals.judge import StrictJudge
from evals.runner import run_suite
from evals.schema import BAD_JSON, RAISE, AgentCase
from evals.scorers import ERROR, OK, SKIPPED, ContainsScorer, LlmJudgeScorer, ToolCallScorer
from harness.tools.base import ToolRegistry
from harness.tools.builtins.calculator import CalculatorTool


def _case(cid="a1", message="帮我算 (12+8)*3", **expect):
    return AgentCase(id=cid, input={"message": message}, expect=expect)


def _driver(client):
    reg = ToolRegistry()
    reg.register(CalculatorTool())
    return AgentDriver(client=client, registry=reg, system_prompt="你是助手", max_steps=5)


def _judge(score=95):
    return StrictJudge(scripted_complete({"质检": {"score": score, "feedback": "还行"}}))


# ---- AgentDriver 的事件收集 ----

async def test_driver_collects_final_text(make_mock, text_turn):
    trace = await _driver(make_mock([text_turn("答案是 60")])).run(_case())
    assert trace.final == "答案是 60"
    assert trace.error == "" and trace.tools == []


async def test_driver_collects_tool_trajectory(make_mock, tool_turn, text_turn):
    client = make_mock([tool_turn("calculator", '{"expression": "(12+8)*3"}'),
                        text_turn("结果是 60")])
    trace = await _driver(client).run(_case())
    assert trace.tools == ["calculator"]
    assert trace.final == "结果是 60"
    # steps 形状须对齐 chat.py collect["steps"]，否则喂不进 _tool_exec_summary
    assert trace.steps[0]["tool"] == "calculator"
    assert trace.steps[0]["is_error"] is False
    assert "60" in trace.steps[0]["result"]


async def test_driver_steps_feed_tool_exec_summary(make_mock, tool_turn, text_turn):
    """steps 能被线上那份 _tool_exec_summary 直接吃 —— 离线喂给 judge 的上下文与线上一致。"""
    from app.verify import _tool_exec_summary
    client = make_mock([tool_turn("calculator", '{"expression": "1+1"}'), text_turn("2")])
    trace = await _driver(client).run(_case())
    summary = _tool_exec_summary(trace.steps)
    assert "calculator" in summary and "成功" in summary


async def test_driver_records_run_error(make_mock, tool_turn):
    # 只给工具轮、不给收尾文本 → 撞 max_steps → RunError
    client = make_mock([tool_turn("calculator", '{"expression": "1+1"}', call_id=f"c{i}")
                        for i in range(6)])
    trace = await _driver(client).run(_case())
    assert trace.error, "撞步数上限应记为 error"


# ---- 打分器 ----

async def test_contains_scorer_passes_and_fails(make_mock, text_turn):
    d = _driver(make_mock([text_turn("答案是 60")]))
    trace = await d.run(_case())
    assert (await ContainsScorer().score(_case(must_contain=["60"]), trace)).value == 1.0
    s = await ContainsScorer().score(_case(must_contain=["99"]), trace)
    assert s.value == 0.0 and "缺" in s.detail


async def test_contains_scorer_must_not_contain(make_mock, text_turn):
    trace = await _driver(make_mock([text_turn("我不知道")])).run(_case())
    s = await ContainsScorer().score(_case(must_not_contain=["不知道"]), trace)
    assert s.value == 0.0


async def test_contains_scorer_skipped_without_expectation(make_mock, text_turn):
    trace = await _driver(make_mock([text_turn("随便")])).run(_case())
    assert (await ContainsScorer().score(_case(), trace)).status == SKIPPED


async def test_tool_call_scorer(make_mock, tool_turn, text_turn):
    client = make_mock([tool_turn("calculator", '{"expression": "1+1"}'), text_turn("2")])
    trace = await _driver(client).run(_case())
    assert (await ToolCallScorer().score(_case(must_call_tools=["calculator"]), trace)).value == 1.0
    s = await ToolCallScorer().score(_case(must_call_tools=["search_knowledge"]), trace)
    assert s.value == 0.0 and "未调用" in s.detail


async def test_llm_judge_scorer_normalizes_score(make_mock, text_turn):
    trace = await _driver(make_mock([text_turn("答案是 60")])).run(_case())
    s = await LlmJudgeScorer(_judge(80)).score(_case(), trace)
    assert s.status == OK and s.value == pytest.approx(0.8)


# ---- 与线上语义分道扬镳的验证点 ----

@pytest.mark.parametrize("payload", [RAISE, BAD_JSON])
async def test_judge_failure_is_error_not_pass(make_mock, text_turn, payload):
    """judge 挂了 / 不吐 JSON → ERROR。

    线上同样的情况会放行当通过（绝不因抖动拦交付，见 app/verify.py:_judge_score），
    离线必须相反 —— 否则端点抖动会被读成质量满分。
    """
    trace = await _driver(make_mock([text_turn("答案是 60")])).run(_case())
    judge = StrictJudge(scripted_complete({"质检": payload}))
    s = await LlmJudgeScorer(judge).score(_case(), trace)
    assert s.status == ERROR and "judge 不可用" in s.detail


async def test_agent_failure_is_error_not_zero(make_mock, tool_turn):
    """agent 自己跑挂 → ERROR，不是 0 分（0 分意味着「答得差」，那是另一回事）。"""
    client = make_mock([tool_turn("calculator", '{"expression": "1+1"}', call_id=f"c{i}")
                        for i in range(6)])
    trace = await _driver(client).run(_case())
    s = await ContainsScorer().score(_case(must_contain=["60"]), trace)
    assert s.status == ERROR


async def test_judge_samples_takes_median(make_mock, text_turn):
    """samples>1 取中位数（兑现 config.judge_samples 那个一直没用上的预留槽位）。"""
    trace = await _driver(make_mock([text_turn("答案")])).run(_case())
    scores = iter([10, 90, 80])

    async def complete(system, user):
        import json
        return json.dumps({"score": next(scores), "feedback": ""})

    s = await LlmJudgeScorer(StrictJudge(complete, samples=3)).score(_case(), trace)
    assert s.value == pytest.approx(0.8), "10/90/80 的中位数是 80，不是均值 60"


# ---- 整条管线 ----

async def test_full_pipeline_through_run_suite(make_mock, tool_turn, text_turn):
    client = make_mock([tool_turn("calculator", '{"expression": "(12+8)*3"}'),
                        text_turn("结果是 60")])
    cases = [_case(must_contain=["60"], must_call_tools=["calculator"])]
    report = await run_suite(cases, _driver(client),
                             [ContainsScorer(), ToolCallScorer(), LlmJudgeScorer(_judge(95))],
                             suite="agent-mock")
    m = report.metrics()
    assert m["contains"]["mean"] == 1.0
    assert m["tool_call"]["mean"] == 1.0
    assert m["llm_judge"]["mean"] == pytest.approx(0.95)
    assert report.error_rate() == 0.0

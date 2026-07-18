# app/orchestration/usage_ctx.py
"""编排器 token 用量累加器（contextvar）。

编排器一次 run 会散出多个子调用：planner/critic 走 call_json completer（返回字符串、不走事件流），
executor/synthesize 走 AgentLoop。它们各自的 ModelUsage 原本被吞。用一个每轮独立的累加器把它们
汇总，run() 末尾发一条总的 ModelUsage，前端才显示得出总 tokens/成本。

用 contextvar 传播：run() 在自己的上下文里 set 一个可变 UsageAcc；executor 的并行 worker 由
asyncio.create_task 拷贝上下文，拷的是同一个 UsageAcc 对象引用，故 worker 里 record 也累加到同
一份（asyncio 单线程，add 是同步的，无需加锁）。record_usage 在无累加器时是 no-op，故 completer
被非编排器路径复用时零影响。
"""
from __future__ import annotations

import contextvars

from harness.usage import Usage


class UsageAcc:
    def __init__(self) -> None:
        self.usage = Usage()
        self.cost = 0.0

    def add(self, usage: Usage, cost: float | None) -> None:
        self.usage = self.usage + usage
        self.cost += cost or 0.0


_acc: contextvars.ContextVar = contextvars.ContextVar("orchestrator_usage_acc", default=None)


def record_usage(usage: Usage, cost: float | None) -> None:
    """把一次模型调用的用量记进当前累加器；无累加器（非编排器路径）时 no-op。"""
    acc = _acc.get()
    if acc is not None:
        acc.add(usage, cost)


def set_acc(acc: UsageAcc):
    return _acc.set(acc)


def reset_acc(token) -> None:
    _acc.reset(token)


# ---- 思考(ReasoningDelta)sink：让走 completer 的子调用（如 planner）的思考能被捕获转发 ----
# completer 返回字符串、吞掉 ReasoningDelta；planner 走 call_json→build_completer，其"先思考再出
# 严格 JSON"里的思考本来看不到。用一个 sink：build_completer 命中 ReasoningDelta 时 record_reasoning，
# orchestrator 只在 planner 调用期间挂上 sink，把思考在计划之前发出来。无 sink 时 no-op。
_reason_sink: contextvars.ContextVar = contextvars.ContextVar("reason_sink", default=None)


def record_reasoning(text: str) -> None:
    sink = _reason_sink.get()
    if sink is not None:
        sink(text)


def set_reason_sink(fn):
    return _reason_sink.set(fn)


def reset_reason_sink(token) -> None:
    _reason_sink.reset(token)

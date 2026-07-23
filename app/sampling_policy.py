# app/sampling_policy.py
"""「哪个调用点用多少温度」的唯一真源。

此前全项目 22 个 LLM 调用点共用一个 HARNESS_TEMPERATURE=0.7——判分、JSON 抽取、记忆调和
这些要求确定性的活，和闲聊用同一个采样温度，于是同一份答卷两次判分可能给出不同结论。

分三层，越靠后越动态：

1. **角色档**（ROLE_TEMPERATURES）：与用户问什么无关的固定值。判断/结构化输出恒低，
   机械转写偏低，出题与检索改写反而要高（见下方各条注释）。
2. **意图档**（INTENT_TEMPERATURES）：只作用于面向用户生成的那几处（主循环 ReAct、
   简单直答、最终汇总），由 triage 顺带产出的类别决定。
3. **运行期增量**：见 harness/llm/sampling.py 的 temperature_delta，由失败情形驱动。

三层都最终收在 [0,1]（clamp_temperature），配置覆盖也过同一道夹取。
"""
from __future__ import annotations

import logging

from harness.llm.sampling import clamp_temperature

log = logging.getLogger("app.sampling")

# ── 角色档：判断 / 结构化输出（恒定低温）────────────────────────────────────────────
# 这些调用的正确答案唯一，随机性只带来不一致，没有任何收益。
_JUDGEMENT = {
    "triage": 0.0,            # 简单/复杂二分类
    "critic": 0.0,            # 单步 validate + 终局 review
    "grounding": 0.0,         # 交付门事实核对
    "judge": 0.0,             # 交付门打分 / 轨迹分层打分
    "exam_grade": 0.0,        # 考试简答判分
    "quiz_grade": 0.0,        # 刷题判分
    "plan_finalize": 0.0,     # 清单收尾（照工具摘要填终态）
    "memory_reconcile": 0.0,  # 记忆调和：判 REPLACE 会永久作废旧记忆，最忌抖动
    "question_import": 0.0,   # 题目抽取（严格 JSON）
    # 规划不设 0：拆 DAG 要一点组合能力，全确定性反而容易套同一个模板拆错。
    "planner": 0.2,
}

# ── 角色档：机械转写（低温）──────────────────────────────────────────────────────
_MECHANICAL = {
    "summary": 0.2,           # L2 滚动摘要（含二次压缩）
    "titling": 0.2,           # 对话自动命名
    "memory_extract": 0.1,    # 记忆事实提炼（JSON）
    "memory_consolidate": 0.2,  # 记忆整合蒸馏
    # 执行子步带工具循环，不设 0：温度 0 时部分模型更容易卡在重复调同一个工具上
    # （agent_loop 的打转检测治的就是这个），故地板给到 0.2。
    "executor": 0.2,
}

# ── 角色档：反直觉的两处（要高温）──────────────────────────────────────────────────
_DIVERSE = {
    # 同一知识点反复出题，低温会出一模一样的题，刷题就失去意义了。
    "quiz_generate": 0.8,
    # 查询期召回增强：multi-query 的 N 条改写要够散才有扩召回的意义（太低会雷同，等于
    # 白花一次调用），HyDE 的假设文档又要贴着问题走。二者是**同一次调用**产出的两个字段
    # （见 memory/query_planner.py::plan），没法分开设，取折中 0.5。
    "query_plan": 0.5,
}

ROLE_TEMPERATURES: dict[str, float] = {**_JUDGEMENT, **_MECHANICAL, **_DIVERSE}

# ── 意图档：只作用于主循环 / 简单直答 / 最终汇总 ────────────────────────────────────
# 键即 triage 允许输出的类别词（见 orchestrator.TRIAGE_SYSTEM），改这里要同步改那边的枚举。
INTENT_TEMPERATURES: dict[str, float] = {
    "factual": 0.2,      # 事实问答、查资料
    "code": 0.2,         # 写代码、报错排查
    "rewrite": 0.3,      # 翻译、摘要、润色
    "exam": 0.2,         # 考试/刷题轮（有状态流程，指令要照做）
    "creative": 0.9,     # 创作、头脑风暴、起名
    "chat": 0.7,         # 闲聊寒暄——维持项目原有的 0.7
}
# triage 判不出/给了表外的词时用它：回退到项目原本的行为，不赌。
INTENT_FALLBACK = "chat"

# ── 运行期增量：升温档 ──────────────────────────────────────────────────────────
# 「模型陷在同一条路上」时换条采样路径。步长小、档数少——升过 0.9 不是更有创意，
# 是开始出病句，把一个本可救回的重试变成必挂。
DELTA_LOOP_NUDGE = 0.2       # 打转纠偏：与纠偏提示同时给
DELTA_PER_RETRY = 0.15       # 整轮重答 / 单步重试：每多试一次加一档
DELTA_RETRY_MAX = 0.3        # 升温封顶（=两档），再多无益

# ── 运行期增量：降温档 ──────────────────────────────────────────────────────────
# 「输出结构崩了」是采样太散，与上面方向相反。只对**无工具的单发 JSON 调用**用
# （planner / call_json / 题目解析），带工具的循环不降——见 executor 那条注释。
DELTA_PER_PARSE_FAIL = -0.1
DELTA_PARSE_FAIL_MAX = -0.3


def retry_delta(attempt: int) -> float:
    """第 attempt 次重试（attempt 从 0 起=首次尝试）该加多少温度。首次为 0。"""
    return min(DELTA_RETRY_MAX, max(0, attempt) * DELTA_PER_RETRY)


def parse_fail_delta(attempt: int) -> float:
    """第 attempt 次解析失败重试该降多少温度。首次为 0。"""
    return max(DELTA_PARSE_FAIL_MAX, -max(0, attempt) * abs(DELTA_PER_PARSE_FAIL))


def _merged(defaults: dict[str, float], override, what: str) -> dict[str, float]:
    """合并配置覆盖：非法值（非数字）跳过并告警，合法值一律夹到 [0,1]。"""
    out = dict(defaults)
    for k, v in (override or {}).items():
        try:
            out[str(k)] = clamp_temperature(v)
        except (TypeError, ValueError):
            log.warning("%s 里 %s=%r 不是数字，已忽略", what, k, v)
    return out


def role_temperature(config, role: str) -> float | None:
    """角色对应的温度；表里没有则 None（=不覆盖，用 config 基准）。"""
    table = _merged(ROLE_TEMPERATURES, getattr(config, "role_temperatures", None),
                    "HARNESS_ROLE_TEMPERATURES")
    return table.get(role)


def intent_temperature(config, intent: str) -> float:
    """意图对应的温度；未知意图回退 chat（即项目原有的 0.7），绝不因误分类走极端值。"""
    table = _merged(INTENT_TEMPERATURES, getattr(config, "intent_temperatures", None),
                    "HARNESS_INTENT_TEMPERATURES")
    if intent in table:
        return table[intent]
    return table.get(INTENT_FALLBACK, clamp_temperature(getattr(config, "temperature", 0.7)))

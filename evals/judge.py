# evals/judge.py
"""eval 语义的 LLM judge。

与 app/verify.py 的关键差异只有一处，但很要命：线上「LLM/解析失败 → 吞掉当通过」
（绝不因基建抖动拦交付），eval 必须相反 —— 失败要显式变成 Score(status="error")，
否则一次端点抖动会被读成一次满分，而 eval 的全部意义就是分数可信。

prompt 与 JSON 契约完全复用 app/verify.py，不另起一套：judge 换了 prompt 就不是同一个
judge，离线分数与线上分数将不可比。共用的调用/解析走 app.verify.call_json（它会抛，
异常策略留给调用方）。
"""
from __future__ import annotations

from statistics import median

from app.verify import (GROUNDING_SYSTEM, JUDGE_SYSTEM, TRAJECTORY_SYSTEM, _coerce_int,
                        _tool_exec_summary, call_json)

from .scorers import ERROR, OK, Score


class StrictJudge:
    """独立裁判模型打分。complete 由外部注入（真实层传 build_judge_completer 的产物）。

    samples > 1 时多次采样取中位数 —— 兑现 app/config.py 里 judge_samples 那个一直没用上的
    预留槽位。LLM judge 的单次打分方差不小，取中位数能压掉离群值；但压不到零，所以真实层
    永远不当 PR 门禁。
    """

    def __init__(self, complete, *, samples: int = 1) -> None:
        self._complete = complete
        self._samples = max(1, samples)

    async def _score_n(self, system: str, user: str, pick) -> tuple[float | None, str]:
        """跑 samples 次取中位数。任一次抛异常都直接向上抛（由 scorer 转成 ERROR）。"""
        vals: list[float] = []
        feedback = ""
        for _ in range(self._samples):
            v = await call_json(self._complete, system, user)
            got = pick(v)
            if got is None:
                raise ValueError(f"judge 返回里取不到分数：{v!r}")
            vals.append(float(got))
            feedback = feedback or (v.get("feedback") or "")
        return median(vals), feedback

    async def score_answer(self, question: str, answer: str,
                           steps: list[dict] | None = None,
                           rubric: str = "") -> Score:
        """答案质量分（0-100 → 归一化 0-1）。与线上 AnswerVerifier._judge_score 同 prompt 同口径。"""
        tools = _tool_exec_summary(steps)
        parts = [f"用户问题：{question}"]
        if tools:
            parts.append(f"AI 为完成此任务调用的工具及结果（成果可能在此、而非正文）：\n{tools}")
        parts.append(f"回答：\n{answer}")
        if rubric:
            parts.append(f"评分要点：\n{rubric}")
        parts.append("请先找问题再打分并点评。")
        try:
            score, fb = await self._score_n(JUDGE_SYSTEM, "\n".join(parts),
                                            lambda v: _coerce_int(v.get("score")))
        except Exception as e:
            # 这里正是与线上分道扬镳的地方：线上此处 return None 放行，eval 必须记 ERROR
            return Score(0.0, ERROR, f"judge 不可用：{type(e).__name__}: {e}")
        return Score(score / 100.0, OK, fb, raw={"score": score})

    async def score_trajectory(self, question: str, plan: str, step_summaries: str,
                               answer: str) -> Score:
        """轨迹的 final 层分数。plan/steps 层一并放进 raw 供报告展开。"""
        user = (f"用户问题：{question}\n\n"
                f"任务拆分：\n{plan or '（无显式拆分）'}\n\n"
                f"关键步摘要：\n{step_summaries or '（无）'}\n\n"
                f"最终答案：\n{answer}\n\n请分别给 拆分/每步/最终 打分并简评。")
        try:
            v = await call_json(self._complete, TRAJECTORY_SYSTEM, user)
        except Exception as e:
            return Score(0.0, ERROR, f"轨迹 judge 不可用：{type(e).__name__}: {e}")
        final = _coerce_int(v.get("final"))
        if final is None:
            return Score(0.0, ERROR, f"轨迹 judge 未给出 final 分：{v!r}")
        return Score(final / 100.0, OK, v.get("feedback") or "",
                     raw={"plan": _coerce_int(v.get("plan")),
                          "steps": _coerce_int(v.get("steps")), "final": final})

    async def score_grounding(self, context: str, answer: str) -> Score:
        """知识库依据：grounded 为真给 1 分，否则 0 分并列出缺依据的论断。"""
        user = f"知识库资料：\n{context}\n\n待核查回答：\n{answer}\n\n请逐条判断回答是否都有资料支撑。"
        try:
            v = await call_json(self._complete, GROUNDING_SYSTEM, user)
        except Exception as e:
            return Score(0.0, ERROR, f"grounding 判定不可用：{type(e).__name__}: {e}")
        grounded = bool(v.get("grounded", True))
        unsupported = v.get("unsupported") or []
        detail = v.get("feedback") or ""
        if not grounded and unsupported:
            detail = (detail + "；" if detail else "") + \
                f"缺依据：{'、'.join(str(u) for u in unsupported[:3])}"
        return Score(1.0 if grounded else 0.0, OK, detail)

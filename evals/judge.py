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

from app.verify import (GROUNDING_SYSTEM, TRAJECTORY_SYSTEM, _coerce_int,
                        _tool_exec_summary, call_json)

# 离线打分器自己的评分 prompt。原先与线上交付门的 judge 层共用一份（app/verify.py），
# 但那一层已随交付门删除——它与编排器的 Critic.review 做的是同一件事，线上留一个就够。
# 这里保留下来是因为 evals 仍需要一把稳定的尺子给答案质量打分：offline 打分器和线上
# 裁判本就是两回事，前者要可复现、要能显式报错，后者要宽容、绝不因抖动拦交付。
JUDGE_SYSTEM = (
    "你是严格的答案质检员。评估「回答是否达成用户目标」，综合考量相关性、准确性、完整性、安全性。"
    "重要：若任务主要通过工具执行完成（如已生成/保存/查询/下载成功），简洁的完成确认就是恰当的回答，"
    "不要因为「正文没有展开罗列细节」而扣分——以是否真正达成用户意图为准，成果可能体现在工具执行结果里。"
    "对话是多轮的：给出【最近几轮对话】时，必须结合它来解读用户本轮输入——"
    "用户本轮往往是在接着往下回应。若近几轮里 AI 给过选项/清单（如「回复 A/B/C/D」「选一个方案」），"
    "用户回「A」「第二个」「好」「就它」等简短内容就是【明确的选择】，AI 据此直接执行完全正确，"
    "绝不能判成「输入含义不明」「AI 未澄清就动手」——那是没读上下文的误判。"
    "只有在【结合最近对话后】用户输入仍然无效/残缺/有歧义时，AI 才应请求澄清；此时 AI 提示重新输入、"
    "请求澄清或合理追问也是恰当推进，不能因「本轮没有直接给出最终答案」判为未达成；"
    "只有在用户需求明确、AI 却答非所问或无理回避时才算未达成。"
    "只输出 JSON：{\"score\": 0-100 的整数, \"feedback\": \"一句话点评（指出主要问题）\"}，不要多余文字。")


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
        """答案质量分（0-100 → 归一化 0-1）。prompt 见本模块 JUDGE_SYSTEM（线上已无对应层，见其注释）。"""
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

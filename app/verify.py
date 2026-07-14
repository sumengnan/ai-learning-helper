# app/verify.py
"""回答交付前的正确性校验器（Answer Delivery Gate 的判定核心）+ 轨迹 judge。

AnswerVerifier.verify 按配置逐项校验一版候选答案，短路优先低成本项：
  1. format    —— 无 LLM：非空、代码围栏闭合、非空转。【硬门】
  2. grounding —— LLM：本轮检索到的知识库资料是否逐条支撑答案论断（仅当有检索命中）。【软门】
  3. code      —— 在会话沙箱内实跑答案里的代码块（python/node/java），报错即不过。【硬门】
  4. facts     —— 答案中引用的 http(s) 链接是否可达（2xx/3xx）。【软门】
  5. judge     —— 独立 judge 模型 + 挑错视角打分，低于阈值不过。【软门】
任一启用项不过 → ok=False，并合并各项反馈成 critique（供交付门回灌重答）。
hard_failed 记录其中的硬门项：交付门据此决定「硬门失败不降级、必须重答或明确拦截」。

TrajectoryJudge.score 在交付前一次性回看整轨迹（任务拆分 / 关键步 / 最终答案），
用独立模型分层打分，供门控加固与前端质量分展示。

复用：app/completion.py::build_completer（单发 LLM 调用）、app/quiz_service.py 的 _strip_fence。
harness 内核零改动。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from harness.tools.base import ToolError

from .quiz_service import _strip_fence

_log = logging.getLogger("app.verify")

# 硬门：失败不允许降级交付（必须重答或明确拦截）
_HARD_CHECKS = frozenset({"format", "code", "empty"})

GROUNDING_SYSTEM = (
    "你是事实核查员。给你「知识库检索到的资料」和「待核查回答」。"
    "逐条检查回答中的事实性论断是否都能被资料支撑（常识性/推理性内容不算臆造）。"
    "只输出 JSON：{\"grounded\": true 或 false, \"unsupported\": [\"缺依据的论断…\"], "
    "\"feedback\": \"一句话说明\"}，全部有据则 unsupported 为空数组，不要多余文字。")

JUDGE_SYSTEM = (
    "你是严格的答案质检员。评估「回答是否达成用户目标」，综合考量相关性、准确性、完整性、安全性。"
    "重要：若任务主要通过工具执行完成（如已生成/保存/查询/下载成功），简洁的完成确认就是恰当的回答，"
    "不要因为「正文没有展开罗列细节」而扣分——以是否真正达成用户意图为准，成果可能体现在工具执行结果里。"
    "只输出 JSON：{\"score\": 0-100 的整数, \"feedback\": \"一句话点评（指出主要问题）\"}，不要多余文字。")

TRAJECTORY_SYSTEM = (
    "你是严格的过程质检员。给你用户问题、AI 的任务拆分、关键步摘要、最终答案。分别评估："
    "①任务拆分是否合理充分；②各步骤是否有效、无明显跑偏；③最终答案质量（相关/准确/完整）。"
    "各打 0-100 分。只输出 JSON：{\"plan\": int, \"steps\": int, \"final\": int, "
    "\"feedback\": \"一句话总评\"}，无拆分或无步骤时对应字段给 null，不要多余文字。")

# 代码围栏 ```lang\n...\n``` ；lang -> 代码工具名
_FENCE = re.compile(r"```([a-zA-Z0-9_+-]*)[ \t]*\n(.*?)```", re.DOTALL)
_LANG_TOOL = {
    "python": "run_python", "py": "run_python", "python3": "run_python",
    "js": "run_node", "javascript": "run_node", "node": "run_node",
    "java": "run_java",
}
_NO_HIT = "（未在知识库中检索到相关内容）"
_URL_RE = re.compile(r"https?://[^\s)\]}>\"'，。；、]+")
_HTTP_STATUS_RE = re.compile(r"HTTP\s+(\d{3})")


@dataclass
class Verdict:
    ok: bool
    failed: list[str] = field(default_factory=list)   # 未通过的检查名
    critique: str = ""                                # 合并反馈，供重答回灌
    summary: str = ""                                 # 一行摘要，供进度/降级前缀
    hard_failed: list[str] = field(default_factory=list)  # 其中的硬门项

    @classmethod
    def _make(cls, failed: list[str], feedbacks: list[str]) -> "Verdict":
        crit = "；".join(f for f in feedbacks if f)
        return cls(ok=not failed, failed=failed, critique=crit,
                   summary="、".join(failed),
                   hard_failed=[f for f in failed if f in _HARD_CHECKS])


def _looks_truncated(answer: str) -> bool:
    return answer.count("```") % 2 == 1        # 代码围栏未闭合 → 大概率被截断


def _extract_code_blocks(answer: str) -> list[tuple[str, str]]:
    """抽取答案里可运行的代码块，返回 (工具名, 代码) 列表。跳过明显非自包含的片段。"""
    out: list[tuple[str, str]] = []
    for lang, body in _FENCE.findall(answer):
        tool = _LANG_TOOL.get(lang.lower())
        code = body.strip()
        if not tool or not code:
            continue
        if "..." in code or "…" in code:      # 省略占位的示例，非自包含 → 跳过
            continue
        out.append((tool, code))
    return out


def _extract_urls(answer: str) -> list[str]:
    seen: list[str] = []
    for u in _URL_RE.findall(answer or ""):
        u = u.rstrip(".,)]}")
        if u not in seen:
            seen.append(u)
    return seen


def _tool_exec_summary(steps: list[dict] | None, max_each: int = 120) -> str:
    """把工具调用轨迹压成「工具名（成功/失败）：结果概要」，供 judge 理解回答背后的成果。

    工具执行型任务（生成/保存/检索）的实质成果在工具结果里，最终正文常只是简短确认；
    给 judge 补上这份摘要，避免它因「正文简略」误判低分。"""
    if not steps:
        return ""
    lines = []
    for s in steps:
        mark = "失败" if s.get("is_error") else "成功"
        tool = s.get("tool", "?")
        result = (s.get("result") or "").strip().replace("\n", " ")
        if len(result) > max_each:
            result = result[:max_each] + "…"
        lines.append(f"- {tool}（{mark}）：{result}" if result else f"- {tool}（{mark}）")
    return "\n".join(lines)


@dataclass
class TrajectoryScore:
    plan: int | None
    steps: int | None
    final: int | None
    feedback: str = ""


def _coerce_int(x) -> int | None:
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return int(x)
    if isinstance(x, str) and x.strip().lstrip("-").isdigit():
        return int(x.strip())
    return None


class TrajectoryJudge:
    """交付前一次性回看整轨迹，对 拆分/关键步/最终 分层打分（独立模型）。"""

    def __init__(self, complete, config) -> None:
        self._complete = complete
        self._config = config

    async def score(self, question: str, plan: str, step_summaries: str,
                    answer: str) -> TrajectoryScore:
        user = (f"用户问题：{question}\n\n"
                f"任务拆分：\n{plan or '（无显式拆分）'}\n\n"
                f"关键步摘要：\n{step_summaries or '（无）'}\n\n"
                f"最终答案：\n{answer}\n\n请分别给 拆分/每步/最终 打分并简评。")
        try:
            raw = await self._complete(TRAJECTORY_SYSTEM, user)
            v = json.loads(_strip_fence(raw))
            return TrajectoryScore(
                _coerce_int(v.get("plan")), _coerce_int(v.get("steps")),
                _coerce_int(v.get("final")), v.get("feedback") or "")
        except Exception as e:                 # 解析失败/基建抖动 → 跳过评分，不拦截
            _log.warning("轨迹 judge 失败，跳过评分：%s", e)
            return TrajectoryScore(None, None, None, "")


class AnswerVerifier:
    def __init__(self, complete, config, judge_complete=None) -> None:
        self._complete = complete          # async (system_prompt, user_prompt) -> str
        # 独立 judge 模型（降低「自评打高分」偏差）；未注入则回退主 completer
        self._judge_complete = judge_complete or complete
        self._config = config

    async def verify(self, question: str, answer: str, grounding: list[dict],
                     registry, steps: list[dict] | None = None) -> Verdict:
        cfg = self._config
        failed: list[str] = []
        feedbacks: list[str] = []
        ans = (answer or "").strip()

        # 1) format —— 无 LLM，最先短路
        if cfg.gate_check_format:
            if not ans:
                return Verdict._make(["format"], ["回答为空"])
            if _looks_truncated(ans):
                return Verdict._make(["format"], ["回答疑似被截断（代码围栏未闭合）"])

        # 2) grounding —— 仅当本轮检索到知识库资料时才判
        if cfg.gate_check_grounding:
            passages = [g["content"] for g in grounding
                        if g.get("tool") == "search_memory" and not g.get("is_error")
                        and g.get("content") and _NO_HIT not in g["content"]]
            if passages:
                ok, fb = await self._judge_grounding("\n\n".join(passages), ans)
                if not ok:
                    failed.append("grounding")
                    feedbacks.append(fb or "回答存在缺乏知识库依据的论断")

        # 3) code —— 在会话沙箱内实跑答案里的代码块
        if cfg.gate_check_code and registry is not None:
            errs = await self._run_code_blocks(ans, registry)
            if errs:
                failed.append("code")
                feedbacks.append("代码未跑通：" + "；".join(errs))

        # 4) facts —— 引用链接可达性
        if cfg.gate_check_facts and registry is not None:
            bad = await self._check_facts(ans, registry)
            if bad:
                failed.append("facts")
                feedbacks.append("引用链接不可达：" + "；".join(bad))

        # 5) judge —— 独立模型 + 挑错视角打分
        if cfg.gate_check_judge:
            score, fb = await self._judge_score(question, ans, steps)
            if score is not None and score < cfg.answer_pass_score:
                failed.append("judge")
                feedbacks.append(fb or f"质量评分 {score} 低于阈值 {cfg.answer_pass_score}")

        return Verdict._make(failed, feedbacks)

    async def _judge_grounding(self, context: str, answer: str) -> tuple[bool, str]:
        user = f"知识库资料：\n{context}\n\n待核查回答：\n{answer}\n\n请逐条判断回答是否都有资料支撑。"
        try:
            raw = await self._complete(GROUNDING_SYSTEM, user)
            v = json.loads(_strip_fence(raw))
            grounded = bool(v.get("grounded", True))
            unsupported = v.get("unsupported") or []
            fb = v.get("feedback") or ""
            if not grounded and unsupported:
                joined = "、".join(str(u) for u in unsupported[:3])
                fb = (fb + "；" if fb else "") + f"缺依据：{joined}"
            return grounded, fb
        except Exception as e:                 # LLM/解析失败 → 不因基础设施抖动拦截交付
            _log.warning("grounding 校验失败，跳过该项：%s", e)
            return True, ""

    async def _judge_score(self, question: str, answer: str,
                           steps: list[dict] | None = None) -> tuple[int | None, str]:
        tools = _tool_exec_summary(steps)
        parts = [f"用户问题：{question}"]
        if tools:
            parts.append(f"AI 为完成此任务调用的工具及结果（成果可能在此、而非正文）：\n{tools}")
        parts.append(f"回答：\n{answer}")
        parts.append("请先找问题再打分并点评。")
        user = "\n".join(parts)
        try:
            raw = await self._judge_complete(JUDGE_SYSTEM, user)
            v = json.loads(_strip_fence(raw))
            return _coerce_int(v.get("score", 100)), v.get("feedback") or ""
        except Exception as e:
            _log.warning("judge 校验失败，跳过该项：%s", e)
            return None, ""

    async def _run_code_blocks(self, answer: str, registry) -> list[str]:
        errs: list[str] = []
        for tool_name, code in _extract_code_blocks(answer):
            tool = registry.get(tool_name)
            if tool is None:                   # 沙箱/该语言工具未启用 → 跳过
                continue
            try:
                await tool.run(tool.Params(code=code))
            except ToolError as e:             # 非零退出/超时 → 记为不过
                errs.append(f"{tool_name}: {str(e)[:200]}")
            except Exception as e:             # 沙箱不可达等基础设施问题 → 不拦截，仅记日志
                _log.warning("code 校验执行 %s 失败，跳过：%s", tool_name, e)
        return errs

    async def _check_facts(self, answer: str, registry) -> list[str]:
        """抽取答案中的 http(s) 链接，用 http_request 工具判可达（2xx/3xx）。

        工具不存在、参数不匹配、抓取抛错等基础设施问题一律跳过（放行不拦截）；
        仅当明确取到 4xx/5xx 状态码时记为不可达。
        """
        tool = registry.get("http_request")
        if tool is None:
            return []
        bad: list[str] = []
        for url in _extract_urls(answer)[:5]:      # 至多核查前 5 个，控成本
            try:
                out = await tool.run(tool.Params(url=url))
            except Exception as e:                 # 抓取失败/被拒/参数不符 → 放行不判
                _log.warning("facts 校验抓取 %s 失败，跳过：%s", url, e)
                continue
            m = _HTTP_STATUS_RE.search(out or "")
            if m and m.group(1)[0] in ("4", "5"):
                bad.append(f"{url}(HTTP {m.group(1)})")
        return bad

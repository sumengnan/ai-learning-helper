# app/verify.py
"""回答交付前的正确性校验器（Answer Delivery Gate 的判定核心）。

AnswerVerifier.verify 按配置逐项校验一版候选答案，短路优先低成本项：
  1. format   —— 无 LLM：非空、代码围栏闭合、非空转。
  2. grounding —— LLM：本轮检索到的知识库资料是否支撑答案的事实性论断（仅当有检索命中时）。
  3. code     —— 在会话沙箱内实跑答案里的代码块（python/node/java），报错即不过。
  4. judge    —— LLM 自评打分，低于阈值不过。
任一启用项不过 → ok=False，并合并各项反馈成 critique（供交付门回灌重答）。

复用：app/completion.py::build_completer（单发 LLM 调用）、app/quiz_service.py 的
GRADE_SYSTEM/_strip_fence 结构化输出范式。harness 内核零改动。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from harness.tools.base import ToolError

from .quiz_service import _strip_fence

_log = logging.getLogger("app.verify")

GROUNDING_SYSTEM = (
    "你是事实核查员。给你「知识库检索到的资料」和「待核查回答」。"
    "判断回答中的事实性论断是否都能被资料支撑（常识性/推理性内容不算臆造）。"
    "只输出 JSON：{\"grounded\": true 或 false, \"feedback\": \"一句话说明哪些论断缺依据\"}，"
    "不要多余文字。")

JUDGE_SYSTEM = (
    "你是严格的答案质检员。对照用户问题给回答打分，综合考量相关性、准确性、完整性、"
    "是否有害。只输出 JSON：{\"score\": 0-100 的整数, \"feedback\": \"一句话点评\"}，"
    "不要多余文字。")

# 代码围栏 ```lang\n...\n``` ；lang -> 代码工具名
_FENCE = re.compile(r"```([a-zA-Z0-9_+-]*)[ \t]*\n(.*?)```", re.DOTALL)
_LANG_TOOL = {
    "python": "run_python", "py": "run_python", "python3": "run_python",
    "js": "run_node", "javascript": "run_node", "node": "run_node",
    "java": "run_java",
}
_NO_HIT = "（未在知识库中检索到相关内容）"


@dataclass
class Verdict:
    ok: bool
    failed: list[str] = field(default_factory=list)   # 未通过的检查名（format/grounding/code/judge）
    critique: str = ""                                # 合并反馈，供重答回灌
    summary: str = ""                                 # 一行摘要，供进度/降级前缀

    @classmethod
    def _make(cls, failed: list[str], feedbacks: list[str]) -> "Verdict":
        crit = "；".join(f for f in feedbacks if f)
        return cls(ok=not failed, failed=failed, critique=crit, summary="、".join(failed))


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


class AnswerVerifier:
    def __init__(self, complete, config) -> None:
        self._complete = complete          # async (system_prompt, user_prompt) -> str
        self._config = config

    async def verify(self, question: str, answer: str, grounding: list[dict],
                     registry) -> Verdict:
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

        # 4) judge —— LLM 自评打分
        if cfg.gate_check_judge:
            score, fb = await self._judge_score(question, ans)
            if score is not None and score < cfg.answer_pass_score:
                failed.append("judge")
                feedbacks.append(fb or f"质量评分 {score} 低于阈值 {cfg.answer_pass_score}")

        return Verdict._make(failed, feedbacks)

    async def _judge_grounding(self, context: str, answer: str) -> tuple[bool, str]:
        user = f"知识库资料：\n{context}\n\n待核查回答：\n{answer}\n\n请判断回答是否都有资料支撑。"
        try:
            raw = await self._complete(GROUNDING_SYSTEM, user)
            v = json.loads(_strip_fence(raw))
            return bool(v.get("grounded", True)), v.get("feedback") or ""
        except Exception as e:                 # LLM/解析失败 → 不因基础设施抖动拦截交付
            _log.warning("grounding 校验失败，跳过该项：%s", e)
            return True, ""

    async def _judge_score(self, question: str, answer: str) -> tuple[int | None, str]:
        user = f"用户问题：{question}\n回答：\n{answer}\n请打分并点评。"
        try:
            raw = await self._complete(JUDGE_SYSTEM, user)
            v = json.loads(_strip_fence(raw))
            return int(v.get("score", 100)), v.get("feedback") or ""
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

# app/verify.py
"""回答交付前的正确性校验器（Answer Delivery Gate 的判定核心）+ 轨迹 judge。

AnswerVerifier.verify 按配置逐项校验一版候选答案，短路优先低成本项：
  1. format    —— 无 LLM：非空、代码围栏闭合、非空转。【硬门】
  2. grounding —— LLM：本轮检索到的资料（知识库 或 联网检索）是否逐条支撑答案论断（仅当有检索命中）。【软门】
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

from harness.llm.openai_compat import json_output
from harness.tools.base import ToolError
from harness.tools.builtins.memory_search import NO_KNOWLEDGE_HIT

from .quiz_service import _strip_fence
from .url_blocklist import UrlBlockedError

_log = logging.getLogger("app.verify")


async def call_json(complete, system: str, user: str) -> dict:
    """强制 JSON 输出跑一次 LLM 并解析。任何失败直接抛 —— 异常策略由调用方决定。

    线上（本文件各 judge 方法）：捕获后吞掉当通过，绝不因基建抖动拦交付。
    离线（evals/judge.py::StrictJudge）：不捕获，让失败显式变成 Score(status="error")，
    否则一次端点抖动会被读成一次满分。
    两种语义共用同一份 prompt 与解析，判分口径才可比。
    """
    with json_output():
        raw = await complete(system, user)
    return json.loads(_strip_fence(raw))

# 硬门：失败不允许降级交付（必须重答或明确拦截）
_HARD_CHECKS = frozenset({"format", "code", "empty"})

# 校验层名 → 中文，供前端展示「未通过的是哪一层」
_LAYER_ZH = {
    "format": "格式/完整性", "grounding": "检索依据", "code": "代码可运行",
    "judge": "质量评分", "facts": "引用链接", "trajectory": "整体质量", "empty": "未产出答案",
}


def failed_layers_zh(failed: list[str]) -> str:
    """把未通过的校验层名翻成中文（如 judge→质量评分），供前端展示哪层没过。"""
    return "、".join(_LAYER_ZH.get(f, f) for f in failed)

GROUNDING_SYSTEM = (
    "你是事实核查员。给你「检索到的资料」（可能来自用户知识库，也可能来自联网检索）和"
    "「待核查回答」。只核查回答里【关于主题的客观事实性陈述】是否能被资料支撑。"
    "以下内容不属于核查范围，一律不算缺依据、绝不要列入 unsupported："
    "问候语与开场白、收尾语与鼓励的话、给用户的建议/操作提示/下一步指引、"
    "AI 对自己将做或已做什么的说明、常识、基于资料的合理推理与分析、"
    "以及对资料的忠实改写/归纳/重组/摘要/提炼（如把知识库内容整理成学习笔记、总结、提纲——"
    "这类只是把既有资料换种方式组织呈现，不是新增事实）。"
    "另外：检索到的资料往往只是相关资料的一部分，不要因为某句话没在这批片段里逐字出现就判缺依据。"
    "仅当某条【关于主题的事实陈述】与资料明显矛盾、或明显是资料之外凭空捏造的新事实时，才判为缺依据。"
    "只输出 JSON：{\"grounded\": true 或 false, \"unsupported\": [\"缺依据的事实论断…\"], "
    "\"feedback\": \"一句话说明\"}；全部有据、或回答里只有上述无需核查的内容时，"
    "unsupported 为空数组、grounded 为 true。不要多余文字。")

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

TRAJECTORY_SYSTEM = (
    "你是严格的过程质检员。给你用户问题、AI 的任务拆分、关键步摘要（含每步工具及结果）、最终答案。"
    "分别评估：①任务拆分是否合理充分；②各步骤是否有效、无明显跑偏；③最终答案质量（相关/准确/完整）。"
    "注意：若任务主要通过工具执行完成，关键步的工具成功即代表目标达成，最终答案简洁确认也属恰当，"
    "以是否达成用户意图为准，不要因「正文简略」或「无显式拆分」而过度扣分。"
    "多轮对话中，针对用户无效/残缺/有歧义输入的澄清、请其重输或合理追问也属恰当，不因此扣分。"
    "各打 0-100 分。只输出 JSON：{\"plan\": int, \"steps\": int, \"final\": int, "
    "\"feedback\": \"一句话总评\"}，无拆分或无步骤时对应字段给 null，不要多余文字。"
    "feedback 是【直接展示给用户看】的一句话总评：只谈这次回答本身好在哪/差在哪，"
    "用平实中文写。绝不要提及 plan/steps/final/feedback 等字段名、不要提 null、"
    "不要描述你自己的输出格式——「拆分字段为null」这类是在讲 JSON，不是在评价回答。"
    "没有拆分就不必在 feedback 里解释为什么没有，直接评价实际做了的事。")

# 代码围栏 ```lang\n...\n``` ；lang -> 代码工具名
_FENCE = re.compile(r"```([a-zA-Z0-9_+-]*)[ \t]*\n(.*?)```", re.DOTALL)
_LANG_TOOL = {
    "python": "run_python", "py": "run_python", "python3": "run_python",
    "js": "run_node", "javascript": "run_node", "node": "run_node",
    "java": "run_java",
}
_NO_HIT = NO_KNOWLEDGE_HIT   # 知识库空命中哨兵：取自内核，勿重抄字面量
_GROUNDING_CONTEXT_MAX = 12000     # grounding 核查上下文上限（含知识库+联网），防撑爆核查模型


def _dedup_join(passages: list[str]) -> str:
    """按出现顺序拼接检索片段，去掉完全相同的重复（多次搜索常返回同一条）。"""
    seen: set[str] = set()
    out: list[str] = []
    for p in passages:
        key = (p or "").strip()
        if key and key not in seen:
            seen.add(key)
            out.append(p)
    return "\n\n".join(out)


def _cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…（检索资料过长已截断）"
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
            v = await call_json(self._complete, TRAJECTORY_SYSTEM, user)
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
                     registry, steps: list[dict] | None = None,
                     recent_dialogue: str = "") -> Verdict:
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

        # 2) grounding —— 触发条件仍是「本轮命中了知识库」（不扩大到纯联网轮，避免给大量
        # 联网问答新增 grounding 噪音）。但核查上下文要把【联网检索的结果也算进去】：否则
        # 「知识库+联网」混用时，联网来的事实会因不在知识库而被误判缺依据（本次要修的 bug）。
        if cfg.gate_check_grounding:
            def _live(entries):
                return [g["content"] for g in entries if not g.get("is_error")
                        and g.get("content") and _NO_HIT not in g["content"]]
            kb = _live([g for g in grounding if g.get("tool") == "search_knowledge"])
            web = _live([g for g in grounding
                         if g.get("retrieval") and g.get("tool") != "search_knowledge"])
            # 本轮经 read_attachment/read_file 读入的文档正文：整理成笔记/总结时模型据以作答的
            # 依据，也纳入核查资料——否则「整理知识库成笔记」会因笔记内容不在本轮 top-k search_knowledge
            # 片段里而被误判缺依据。不带 retrieval 标记，故不单独触发 grounding，仅在本轮另有知识库
            # 命中时作为核查上下文。
            docs = _live([g for g in grounding
                          if g.get("tool") in ("read_attachment", "read_file")])
            if kb:                                    # 有知识库依据才做 grounding
                # 知识库 + 读入文档 + 联网 一并作为核查资料；可能很长，拼接去重后截断
                context = _cap(_dedup_join(kb + docs + web), _GROUNDING_CONTEXT_MAX)
                ok, fb = await self._judge_grounding(context, ans)
                if not ok:
                    failed.append("grounding")
                    feedbacks.append(fb or "回答存在缺乏检索依据的论断")

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
            score, fb = await self._judge_score(question, ans, steps, recent_dialogue)
            if score is not None and score < cfg.answer_pass_score:
                failed.append("judge")
                feedbacks.append(fb or f"质量评分 {score} 低于阈值 {cfg.answer_pass_score}")

        return Verdict._make(failed, feedbacks)

    async def _judge_grounding(self, context: str, answer: str) -> tuple[bool, str]:
        user = f"检索资料：\n{context}\n\n待核查回答：\n{answer}\n\n请逐条判断回答是否都有资料支撑。"
        try:
            v = await call_json(self._complete, GROUNDING_SYSTEM, user)
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
                           steps: list[dict] | None = None,
                           recent_dialogue: str = "") -> tuple[int | None, str]:
        tools = _tool_exec_summary(steps)
        parts = []
        if recent_dialogue:
            # 最近几轮对话是解读用户本轮输入的关键上下文：没有它，"A"/"好"/"第二个"
            # 这类简短回复会被误判为含义不明，反过来怪 AI 没澄清就动手（真实误判）。
            parts.append(f"【最近几轮对话（用户本轮在接着往下回应）】：\n{recent_dialogue}")
        parts.append(f"用户本轮输入：{question}")
        if tools:
            parts.append(f"AI 为完成此任务调用的工具及结果（成果可能在此、而非正文）：\n{tools}")
        parts.append(f"回答：\n{answer}")
        parts.append("请先找问题再打分并点评。")
        user = "\n".join(parts)
        try:
            v = await call_json(self._judge_complete, JUDGE_SYSTEM, user)
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
        仅当明确取到 4xx/5xx 状态码、或链接命中失败登记时记为不可达。
        """
        tool = registry.get("http_request")
        if tool is None:
            return []
        bad: list[str] = []
        for url in _extract_urls(answer)[:5]:      # 至多核查前 5 个，控成本
            try:
                out = await tool.run(tool.Params(url=url))
            except UrlBlockedError as e:           # 已知坏链：登记过就是证据，不是基建抖动
                bad.append(f"{url}({e.record['reason']})")
                continue
            except Exception as e:                 # 抓取失败/被拒/参数不符 → 放行不判
                _log.warning("facts 校验抓取 %s 失败，跳过：%s", url, e)
                continue
            m = _HTTP_STATUS_RE.search(out or "")
            if m and m.group(1)[0] in ("4", "5"):
                bad.append(f"{url}(HTTP {m.group(1)})")
        return bad

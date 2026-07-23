# app/verify.py
"""交付后的机械检查（提醒型）+ 轨迹 judge。

**这里不再有「门」**：本模块的检查不拦截、不重答、不判本轮失败，只在答复交付后跑一遍，
把发现的问题作为提醒推给前端（scope=notice）。「答复够不够格」由编排器的 Critic.review
判定——那是唯一的 LLM 裁判，此前交付门的 judge 层与它做的是同一件事，重复了一份 prompt、
一份配置和一整套各自演化的误判修补史（真实代价：「裁判要看最近对话」这个修复只进了
judge、review 漏了几个月）。

DeliveryChecker.run 保留的四项恰恰是 review 结构上做不到的——它只拿到文本，不执行、
不联网、也做不了确定性检测：
  1. format    —— 无 LLM：代码围栏是否闭合（截断迹象）
  2. grounding —— LLM：本轮检索到的资料是否支撑答复里的事实陈述（仅当有知识库命中）
  3. code      —— 在会话沙箱内实跑答复里的代码块（python/node/java）
  4. facts     —— 答复中引用的 http(s) 链接是否可达（2xx/3xx）
任一项发现问题 → 产出一条 Notice，仅供展示与统计。基建抖动一律跳过该项（不产生假提醒）。

TrajectoryJudge.score 在交付后一次性回看整轨迹（任务拆分 / 关键步 / 最终答案），
用独立模型分层打分，供前端质量分展示。同样不驱动重答。

复用：app/completion.py::build_completer（单发 LLM 调用）、app/quiz_service.py 的 _strip_fence。
harness 内核零改动。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

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

# 检查项 → 中文名，供前端与统计展示（键即 Notice.kind）
CHECK_ZH = {
    "format": "完整性", "grounding": "检索依据", "code": "代码可运行", "facts": "引用链接",
}

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
class Notice:
    """一条交付提醒。kind 见 CHECK_ZH；text 是给用户看的中文说明。

    刻意不带 ok/failed 之类的判定字段：它不参与「本轮成不成功」的判断，产生一条
    Notice 只意味着「这里值得你扫一眼」，不意味着答复不合格。
    """
    kind: str
    text: str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "label": CHECK_ZH.get(self.kind, self.kind),
                "text": self.text}


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


class DeliveryChecker:
    """交付后的机械检查。产出 Notice 列表——**不判成败、不触发重答**。

    调用时机是答复已经交付给用户之后（编排器跑完、Critic.review 已经表过态）。故：
    - 任何一项失败都不该让用户白等一次重答；发现问题就如实提醒，由用户自己判断。
    - 基建抖动（沙箱不可达、抓取超时、LLM 解析失败）一律**静默跳过该项**：假提醒比
      不提醒更糟——它会训练用户忽略所有提醒。
    - 各项互相独立、逐项跑完，不像旧交付门那样在 format 处短路：既然不拦截，就没有
      「早点失败省下后面开销」的动机，反倒是把问题一次报全更有用。
    """

    def __init__(self, complete, config) -> None:
        self._complete = complete          # async (system_prompt, user_prompt) -> str
        self._config = config

    async def run(self, answer: str, grounding: list[dict], registry) -> list[Notice]:
        cfg = self._config
        ans = (answer or "").strip()
        notices: list[Notice] = []
        if not ans:
            return notices                 # 没产出答复，交由上游的错误处理表态，不在这里凑热闹

        # 1) format —— 无 LLM
        if cfg.delivery_check_format and _looks_truncated(ans):
            notices.append(Notice("format", "回答疑似被截断（代码围栏未闭合）"))

        # 2) grounding —— 触发条件是「本轮命中了知识库」（不扩大到纯联网轮，避免给大量
        # 联网问答新增噪音）。但核查资料要把【联网检索结果与本轮读入的文档也算进去】：
        # 否则「知识库+联网」混用时，联网来的事实会因不在知识库而被误报缺依据。
        if cfg.delivery_check_grounding:
            def _live(entries):
                return [g["content"] for g in entries if not g.get("is_error")
                        and g.get("content") and _NO_HIT not in g["content"]]
            kb = _live([g for g in grounding if g.get("tool") == "search_knowledge"])
            web = _live([g for g in grounding
                         if g.get("retrieval") and g.get("tool") != "search_knowledge"])
            docs = _live([g for g in grounding
                          if g.get("tool") in ("read_attachment", "read_file")])
            if kb:
                context = _cap(_dedup_join(kb + docs + web), _GROUNDING_CONTEXT_MAX)
                ok, fb = await self._judge_grounding(context, ans)
                if not ok:
                    notices.append(Notice("grounding", fb or "回答存在缺乏检索依据的论断"))

        # 3) code —— 在会话沙箱内实跑答复里的代码块
        if cfg.delivery_check_code and registry is not None:
            errs = await self._run_code_blocks(ans, registry)
            if errs:
                notices.append(Notice("code", "代码未跑通：" + "；".join(errs)))

        # 4) facts —— 引用链接可达性
        if cfg.delivery_check_facts and registry is not None:
            bad = await self._check_facts(ans, registry)
            if bad:
                notices.append(Notice("facts", "引用链接不可达：" + "；".join(bad)))

        return notices

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

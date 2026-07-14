# app/exam_grader.py
"""作答解析与判分。

客观题（单选/多选/判断）：把用户自由文本解析成可比对的作答，再与答案**字面确定性**比对。
简答题：交给 judge 模型判语义对错。判分结果只用于「是否入错题集」的确定性决策——保存本身由
调用方（判分中间件）执行，不经模型自觉。
"""
from __future__ import annotations

import json
import logging
import re

from .quiz_service import _strip_fence

_log = logging.getLogger("app.exam")

# 结束考试意图（判分中间件在判分前先识别，命中则结束而非判分）
END_INTENT_RE = re.compile(r"结束(考试|测验|吧)?|交卷|退出考试|不考了|停止考试|不想考了")

# 判断题正/负向措辞
_TF_FALSE = ("不对", "不正确", "错误", "错", "否", "×", "✗")
_TF_TRUE = ("正确", "对", "是", "√", "✓")
_LETTER_RUN = re.compile(r"[A-Za-z]+")
_NUMBER = re.compile(r"\d+")

SHORT_JUDGE_SYSTEM = (
    "你是简答题判分员。给你题目、参考答案、学生作答。判断学生作答是否答对"
    "（抓住要点、语义等价即算对，不必逐字一致）。"
    "只输出 JSON：{\"correct\": true 或 false, \"feedback\": \"一句话点评\"}，不要多余文字。")

_TYPE_LABEL = {"single": "单选", "multiple": "多选", "truefalse": "判断", "short": "简答"}


def present_question(q: dict, index: int, total: int) -> str:
    """把题目渲染成呈现给用户的文本（不含答案）；选择题标 A/B/C/D。"""
    label = _TYPE_LABEL.get(q["type"], q["type"])
    head = f"第 {index + 1}/{total} 题（{label}）：{q['stem']}"
    opts = q.get("options")
    if q["type"] in ("single", "multiple") and opts:
        lines = [f"{chr(65 + i)}. {o}" for i, o in enumerate(opts)]
        return head + "\n" + "\n".join(lines)
    if q["type"] == "truefalse":
        return head + "（请回答：对／错）"
    return head


def answer_text(q: dict) -> str:
    """正确答案的人类可读文本（判分后揭晓/讲评用）。"""
    typ, ans, opts = q["type"], q.get("answer"), q.get("options") or []
    if typ == "truefalse":
        return "对" if ans else "错"
    if typ == "single" and isinstance(ans, int) and 0 <= ans < len(opts):
        return f"{chr(65 + ans)}. {opts[ans]}"
    if typ == "multiple" and isinstance(ans, list):
        return "、".join(f"{chr(65 + i)}. {opts[i]}" if 0 <= i < len(opts) else str(i) for i in ans)
    return str(ans if ans is not None else "")


# 英文正/负向作答：词边界匹配，容忍前后缀与标点（如「我回答true。」「false!」）
_TF_FALSE_WORD = re.compile(r"(?<![a-zA-Z])(false|no)(?![a-zA-Z])", re.I)
_TF_TRUE_WORD = re.compile(r"(?<![a-zA-Z])(true|yes)(?![a-zA-Z])", re.I)


def _parse_tf(text: str) -> bool | None:
    low = text.strip().lower()
    # 中文措辞用 substring；英文 true/false/yes/no 用词边界（不必整串精确）；
    # 单字母 t/f/y/n/x 仍需整串精确，避免匹配到含该字母的普通词
    if any(k in text for k in _TF_FALSE) or _TF_FALSE_WORD.search(text) or low in ("f", "n", "x"):
        return False
    if any(k in text for k in _TF_TRUE) or _TF_TRUE_WORD.search(text) or low in ("t", "y"):
        return True
    return None


def _parse_indices(text: str, options: list[str]) -> set[int]:
    """从自由文本里识别用户选了哪些选项下标：选项字母 A/B..、序号 1/2..、或选项原文。"""
    n = len(options)
    hits: set[int] = set()
    # 连续字母串（如 "BD"/"ABC"）：仅当每个字母都落在选项范围内才当作选项标号，
    # 否则视为普通单词（如 "CPU"）跳过，交给下面的选项原文匹配。
    for run in _LETTER_RUN.findall(text):
        idxs = [ord(c.upper()) - ord("A") for c in run]
        if idxs and all(0 <= i < n for i in idxs) and len(run) <= n:
            hits.update(idxs)
    for m in _NUMBER.findall(text):
        i = int(m) - 1                       # 序号 1-based
        if 0 <= i < n:
            hits.add(i)
    for i, opt in enumerate(options):
        o = (opt or "").strip()
        if o and o in text:                  # 选项原文出现在作答里
            hits.add(i)
    return hits


def parse_choice(text: str, q: dict):
    """把用户作答解析为可比对形式：判断→bool；单选→下标；多选→下标升序列表。
    客观题解析不出或有歧义 → None（调用方据此要求重答，不判不存不推进）。简答不在此解析。"""
    text = text or ""
    typ = q["type"]
    if typ == "truefalse":
        return _parse_tf(text)
    if typ == "single":
        hits = _parse_indices(text, q.get("options") or [])
        return next(iter(hits)) if len(hits) == 1 else None   # 唯一命中才算；0或歧义→None
    if typ == "multiple":
        hits = _parse_indices(text, q.get("options") or [])
        return sorted(hits) if hits else None
    return None


def grade_objective(q: dict, parsed) -> bool:
    """客观题确定性判分。parsed 需为 parse_choice 的产物。"""
    typ, ans = q["type"], q["answer"]
    if typ == "truefalse":
        return parsed is bool(ans) or parsed == bool(ans)
    if typ == "single":
        return parsed == ans
    if typ == "multiple":
        return sorted(set(parsed or [])) == sorted(set(ans or []))
    return False


async def grade_short(judge_complete, q: dict, user_text: str) -> tuple[bool, str]:
    """简答题：judge 模型判语义对错。judge 不可用/解析失败 → 保守判为不算错（宁漏勿误存）。"""
    user = (f"题目：{q['stem']}\n参考答案：{q.get('answer')}\n"
            f"学生作答：{user_text}\n请判定对错。")
    try:
        raw = await judge_complete(SHORT_JUDGE_SYSTEM, user)
        v = json.loads(_strip_fence(raw))
        return bool(v.get("correct", False)), (v.get("feedback") or "")
    except Exception as e:                    # 基建抖动/解析失败 → 不算错，不误存
        _log.warning("简答判分失败，保守判为不算错：%s", e)
        return True, ""

# app/titling.py
"""把用户的第一句口语提问提炼成简短书面标题（对话自动命名用）。"""
from __future__ import annotations

_SYSTEM = (
    "你是对话标题生成器。把用户的第一句提问提炼成简短的书面标题。"
    "要求：不超过 12 个汉字；用名词短语概括主题；不要标点、引号、书名号；"
    "不要解释或回答问题，只输出标题本身。")

_STRIP = " \t\r\n\"'“”‘’《》【】（）()。，,、!！?？:：；;.…"


def _clean(raw: str, max_len: int) -> str:
    """取首行、剥掉包裹标点、截断到 max_len。"""
    line = next((s for s in (raw or "").splitlines() if s.strip()), "")
    return line.strip(_STRIP)[:max_len]


async def make_title(completer, message: str, max_len: int = 12) -> str:
    """口语提问 → 简短标题。生成失败/为空时回退用原文截断，绝不抛出。"""
    fallback = _clean(message, max_len) or "新对话"
    try:
        raw = await completer(_SYSTEM, message)
    except Exception:
        return fallback
    return _clean(raw or "", max_len) or fallback

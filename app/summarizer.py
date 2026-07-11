# app/summarizer.py
from __future__ import annotations

from typing import Awaitable, Callable

from harness.types import Message, Role
from harness.usage import count_message_tokens

from .summaries import SummaryStore

# 一次性补全器：async (system_prompt, user_prompt) -> str，由 completion.build_completer 提供。
Completer = Callable[[str, str], Awaitable[str]]

_SYS = (
    "你是对话摘要器。把下面的历史对话压缩成简洁连贯的中文摘要，"
    "保留关键事实、用户目标、已达成的结论与未决问题；丢弃寒暄与冗余。"
    "若给出【已有摘要】，把它与【新增对话】合并为一份统一摘要。只输出摘要正文，不要解释。"
)
_COMPRESS_SYS = (
    "下面的摘要过长，请在不丢失关键事实、用户目标与结论的前提下进一步压缩成更短的中文摘要。"
    "只输出摘要正文。"
)


def _render(msgs: list[Message]) -> str:
    """把消息渲染成供摘要的纯文本（工具调用只留名字与文本，图片略去）。"""
    lines: list[str] = []
    for m in msgs:
        text = m.content if isinstance(m.content, str) else (
            " ".join(p.get("text", "") for p in m.content if isinstance(p, dict))
            if isinstance(m.content, list) else "")
        if m.tool_calls:
            names = "、".join(tc.name for tc in m.tool_calls)
            lines.append(f"助手（调用工具 {names}）：{text}".rstrip("："))
        elif m.role == Role.TOOL:
            lines.append(f"工具结果：{text}")
        else:
            lines.append(f"{m.role.value}：{text}")
    return "\n".join(lines)


class RollingSummarizer:
    """L2 增量滚动摘要：只摘水位之后的 delta，合并进已有摘要，成本有界。

    水位 up_to_seq = 已被摘要覆盖的历史消息前缀长度。evicted 恒为 history 的前缀，
    故用前缀长度做水位即可，无需给 Message 挂 seq。
    """

    def __init__(self, store: SummaryStore, complete: Completer, *,
                 model: str, max_summary_tokens: int) -> None:
        self._store = store
        self._complete = complete
        self._model = model
        self._max_tokens = max_summary_tokens

    def _tokens(self, text: str) -> int:
        return count_message_tokens([Message(role=Role.SYSTEM, content=text)], self._model)

    async def ensure(self, conv_id: str, evicted_prefix: list[Message]) -> str | None:
        """确保摘要覆盖到 evicted_prefix。返回当前摘要（无内容时 None）。

        evicted_prefix 是被 L1 挤出窗口的历史前缀。只有当它比水位更长时才调用 LLM。
        """
        if not evicted_prefix:
            return None
        rec = self._store.get(conv_id)
        prev_summary, covered = (rec.summary, rec.up_to_seq) if rec else ("", 0)
        if len(evicted_prefix) <= covered:
            return prev_summary or None      # 已覆盖，跳过 LLM
        delta = evicted_prefix[covered:]
        user_prompt = f"【已有摘要】\n{prev_summary or '（无）'}\n\n【新增对话】\n{_render(delta)}"
        summary = (await self._complete(_SYS, user_prompt)).strip()
        if self._tokens(summary) > self._max_tokens:
            summary = (await self._complete(_COMPRESS_SYS, summary)).strip()
        self._store.upsert(conv_id, up_to_seq=len(evicted_prefix),
                           summary=summary, tokens=self._tokens(summary))
        return summary

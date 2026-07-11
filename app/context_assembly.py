# app/context_assembly.py
from __future__ import annotations

from harness.context.budget import ContextBudget
from harness.context.windowing import WindowStrategy
from harness.types import Message, Role
from harness.usage import count_message_tokens

from .context import ConversationContextManager, LayeredContextManager


class ContextAssembler:
    """按 config.context_strategy 组装上下文管理器。异步预算窗口/摘要/检索，产出一个
    纯同步的 manager 供 AgentLoop 逐步 build。摘要/检索失败一律降级，绝不打断聊天。

    - full：全量拼接（等价历史行为，安全回退）
    - window：仅 L1 token 预算滑动窗口
    - layered：L1 + L2 摘要 + L3 检索
    """

    def __init__(self, config, model: str, *, summarizer=None, conv_memory=None) -> None:
        self._config = config
        self._model = model
        self._summarizer = summarizer
        self._conv_memory = conv_memory

    async def build_manager(self, system_prompt: str, history: list[Message],
                            query: str, conv_id: str):
        strategy = getattr(self._config, "context_strategy", "full")
        if strategy == "full" or not history:
            return ConversationContextManager(system_prompt, history)

        budget = ContextBudget(
            context_window=self._config.context_window_tokens,
            response_reserve=self._config.context_response_reserve_tokens,
            working_ratio=self._config.context_working_ratio)
        sys_tokens = count_message_tokens(
            [Message(role=Role.SYSTEM, content=system_prompt)], self._model)
        window = WindowStrategy(self._model).select(
            history, budget.working_tokens(sys_tokens))

        summary_block = retrieved_block = None
        if strategy == "layered" and window.evicted:
            summary_block = await self._summary_block(conv_id, window.evicted)
            retrieved_block = await self._retrieved_block(
                conv_id, query, len(window.evicted))

        return LayeredContextManager(
            system_prompt, summary_block, retrieved_block, window.kept)

    async def _summary_block(self, conv_id, evicted) -> Message | None:
        if not (self._config.context_enable_summary and self._summarizer):
            return None
        try:
            text = await self._summarizer.ensure(conv_id, evicted)
        except Exception:
            return None                      # 摘要失败不影响本轮
        if not text:
            return None
        return Message(role=Role.SYSTEM,
                       content=f"以下是本对话更早内容的摘要：\n{text}")

    async def _retrieved_block(self, conv_id, query, before_seq) -> Message | None:
        if not (self._config.context_enable_retrieval and self._conv_memory):
            return None
        try:
            hits = await self._conv_memory.retrieve(
                conv_id, query, self._config.context_retrieval_top_k,
                before_seq=before_seq)
        except Exception:
            return None                      # 检索失败不影响本轮
        if not hits:
            return None
        joined = "\n\n".join(h.text for h in hits)
        return Message(role=Role.USER,
                       content=f"【与当前问题相关的更早对话片段，供参考】\n{joined}")

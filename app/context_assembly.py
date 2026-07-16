# app/context_assembly.py
from __future__ import annotations

import logging

from harness.context.budget import ContextBudget
from harness.context.windowing import WindowStrategy
from harness.types import Message, Role
from harness.usage import count_message_tokens

from .context import ConversationContextManager, LayeredContextManager

log = logging.getLogger("app.context")


class ContextAssembler:
    """按 config.context_strategy 组装上下文管理器。异步预算窗口/摘要/检索，产出一个
    纯同步的 manager 供 AgentLoop 逐步 build。摘要/检索失败一律降级，绝不打断聊天。

    - full：全量拼接（等价历史行为，安全回退）
    - window：仅 L1 token 预算滑动窗口
    - layered：L1 + L2 摘要 + L3 检索

    降级不等于无声：L2 摘要失败的后果不轻——被挤出 L1 的历史已经不在上下文里了，摘要
    再没有，这一轮模型就凭空失忆一段，且它不知道自己不知道。故失败必打日志 + 记进 trace，
    让「多少轮该摘没摘成」可被统计到（对齐交付门 gate_error 的做法）。
    """

    def __init__(self, config, model: str, *, summarizer=None, conv_memory=None) -> None:
        self._config = config
        self._model = model
        self._summarizer = summarizer
        self._conv_memory = conv_memory

    async def build_manager(self, system_prompt: str, history: list[Message],
                            query: str, conv_id: str, trace: dict | None = None):
        """trace 非 None 时就地填入本轮上下文组装的结构化结果（供落库/统计）。

        刻意用传入的 dict 而非存在 self 上：本 assembler 在 make_chat_router 里只建一次、
        由所有并发请求共用，任何挂在实例上的状态都会串轮。也刻意不改返回类型：不关心
        trace 的调用方（大量测试）无需改动，trace 是严格可选的旁路。
        """
        strategy = getattr(self._config, "context_strategy", "full")
        if trace is not None:
            trace["strategy"] = strategy
        if strategy == "full" or not history:
            return ConversationContextManager(system_prompt, history)

        budget = ContextBudget(
            context_window=self._config.context_window_tokens,
            response_reserve=self._config.context_response_reserve_tokens,
            working_ratio=self._config.context_working_ratio,
            max_prompt_tokens=getattr(self._config, "context_max_prompt_tokens", 0))
        sys_tokens = count_message_tokens(
            [Message(role=Role.SYSTEM, content=system_prompt)], self._model)
        window = WindowStrategy(self._model).select(
            history, budget.working_tokens(sys_tokens))

        if trace is not None:
            # evicted 是本轮「已经不在上下文里」的历史条数 —— 它同时是 L2 失败的危害度：
            # 摘要没成，这些内容就是净丢失。0 条时摘要根本不该跑，失败也无所谓。
            trace["evicted"] = len(window.evicted)
            trace["kept"] = len(window.kept)

        summary_block = retrieved_block = None
        if strategy == "layered" and window.evicted:
            summary_block = await self._summary_block(conv_id, window.evicted, trace)
            retrieved_block = await self._retrieved_block(
                conv_id, query, len(window.evicted), trace)

        return LayeredContextManager(
            system_prompt, summary_block, retrieved_block, window.kept)

    async def _summary_block(self, conv_id, evicted, trace=None) -> Message | None:
        if not (self._config.context_enable_summary and self._summarizer):
            if trace is not None:
                trace["summary"] = "off"
            return None
        try:
            text = await self._summarizer.ensure(conv_id, evicted)
        except Exception as e:               # noqa: BLE001
            # 摘要失败不影响本轮（照常回答），但绝不能无声：这一轮模型是真丢了 len(evicted)
            # 条历史，而它无从察觉，只会照着残缺的上下文自信作答。
            log.warning("L2 摘要失败，本轮丢失更早的 %d 条历史 conv=%s：%s",
                        len(evicted), conv_id, e, exc_info=True)
            if trace is not None:
                trace["summary"] = "error"
                trace["summary_error"] = f"{type(e).__name__}: {e}"[:200]
            return None
        if not text:
            if trace is not None:
                trace["summary"] = "none"    # 摘要器返回空（如水位已覆盖但库里没内容）
            return None
        if trace is not None:
            trace["summary"] = "ok"
        return Message(role=Role.SYSTEM,
                       content=f"以下是本对话更早内容的摘要：\n{text}")

    async def _retrieved_block(self, conv_id, query, before_seq, trace=None) -> Message | None:
        if not (self._config.context_enable_retrieval and self._conv_memory):
            if trace is not None:
                trace["retrieval"] = "off"
            return None
        try:
            hits = await self._conv_memory.retrieve(
                conv_id, query, self._config.context_retrieval_top_k,
                before_seq=before_seq)
        except Exception as e:               # noqa: BLE001
            # L3 失败没 L2 那么伤：它只是「更早内容的相关片段」这层增益，丢了不等于失忆。
            # 故记为 error 但日志降到 info —— 与 L2 同级会让真正要紧的那条淹掉。
            log.info("L3 检索失败，本轮无相关片段 conv=%s：%s", conv_id, e)
            if trace is not None:
                trace["retrieval"] = "error"
                trace["retrieval_error"] = f"{type(e).__name__}: {e}"[:200]
            return None
        if trace is not None:
            trace["retrieval"] = "ok" if hits else "none"
        if not hits:
            return None
        joined = "\n\n".join(h.text for h in hits)
        return Message(role=Role.USER,
                       content=f"【与当前问题相关的更早对话片段，供参考】\n{joined}")

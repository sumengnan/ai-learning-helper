from __future__ import annotations

import logging
from typing import AsyncIterator

from openai import AsyncOpenAI

from ..config import HarnessConfig
from ..types import Message
from ..usage import Usage, estimate_usage
from .base import StreamChunk, ToolCallDelta

logger = logging.getLogger(__name__)


class OpenAICompatibleClient:
    """基于 openai async SDK 的实现，base_url 可指向任意兼容端点。

    职责：把"消息列表 + 工具 schema"变成归一化的 StreamChunk 流。
    不做重试、不做路由。
    """

    def __init__(self, config: HarnessConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.request_timeout,
        )

    async def stream(
        self, messages: list[Message], tools: list[dict]
    ) -> AsyncIterator[StreamChunk]:
        kwargs: dict = {
            "model": self._config.model,
            "messages": [m.to_openai() for m in messages],
            "temperature": self._config.temperature,
            "stream": True,
        }
        if self._config.include_usage:
            kwargs["stream_options"] = {"include_usage": True}
        if tools:
            kwargs["tools"] = tools
        if self._config.llm_extra_body:
            kwargs["extra_body"] = self._config.llm_extra_body

        completion_parts: list[str] = []
        captured = None
        finish_reason = None
        produced = False           # 本轮是否产出过任何 text / tool_call
        stream = await self._client.chat.completions.create(**kwargs)
        async for event in stream:
            if getattr(event, "usage", None):
                captured = event.usage
            if not event.choices:
                continue
            choice = event.choices[0]
            if getattr(choice, "finish_reason", None):
                finish_reason = choice.finish_reason
            delta = choice.delta
            if getattr(delta, "content", None):
                produced = True
                completion_parts.append(delta.content)
                yield StreamChunk(type="text", text=delta.content)
            for tc in (getattr(delta, "tool_calls", None) or []):
                produced = True
                fn = getattr(tc, "function", None)
                yield StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
                    index=tc.index,
                    id=getattr(tc, "id", None),
                    name=getattr(fn, "name", None) if fn else None,
                    arguments=getattr(fn, "arguments", None) if fn else None,
                ))

        # 空产出留证：上游返回 200 但既无文本也无工具调用（常见于内容安全拦截
        # finish_reason=content_filter、被截断 length，或部分兼容端点把错误塞进 200 流）。
        # 这里记 warning 便于定位「AI 只回…、2 秒完成」这类静默失败的真实上游原因。
        if not produced:
            logger.warning(
                "模型空产出：无 text/tool_call（model=%s, finish_reason=%s）——"
                "可能触发内容安全策略、被截断或上游异常", self._config.model, finish_reason)

        if captured is not None:
            usage = Usage(captured.prompt_tokens, captured.completion_tokens, captured.total_tokens)
        else:
            usage = estimate_usage(messages, "".join(completion_parts), self._config.model)
        yield StreamChunk(type="done", usage=usage)

from __future__ import annotations

from typing import AsyncIterator

from openai import AsyncOpenAI

from ..config import HarnessConfig
from ..types import Message
from .base import StreamChunk, ToolCallDelta


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
        if tools:
            kwargs["tools"] = tools

        stream = await self._client.chat.completions.create(**kwargs)
        async for event in stream:
            if not event.choices:
                continue
            delta = event.choices[0].delta
            if getattr(delta, "content", None):
                yield StreamChunk(type="text", text=delta.content)
            for tc in (getattr(delta, "tool_calls", None) or []):
                fn = getattr(tc, "function", None)
                yield StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
                    index=tc.index,
                    id=getattr(tc, "id", None),
                    name=getattr(fn, "name", None) if fn else None,
                    arguments=getattr(fn, "arguments", None) if fn else None,
                ))
        yield StreamChunk(type="done")

# app/completion.py
from __future__ import annotations

from harness.context.manager import ContextManager
from harness.events import RunError, RunFinished
from harness.loop.agent_loop import AgentLoop
from harness.tools.base import ToolRegistry


def build_completer(client, model_name: str):
    """返回 async (system_prompt, user_prompt) -> str：跑一轮无工具 AgentLoop，取最终文本。

    复用 harness 的重试/预算/OTel 封装；不给 harness 加任何能力。
    """
    async def complete(system_prompt: str, user_prompt: str) -> str:
        loop = AgentLoop(client=client, registry=ToolRegistry(),
                         context=ContextManager(system_prompt),
                         max_steps=1, model_name=model_name)
        final = ""
        async for ev in loop.run(user_prompt):
            if isinstance(ev, RunFinished):
                final = ev.message.content or ""
            elif isinstance(ev, RunError):
                raise RuntimeError(ev.error)
        return final

    return complete


def _judge_config(config):
    """按 judge_* 覆盖返回 judge 专用 config；judge_model 为空则返回 None（回退主模型）。

    参照 embedding/rerank 的回退模式：judge_base_url/judge_api_key 为空时回退主端点，
    因此「只配 judge_model 换裁判模型」和「配独立端点/key」都能工作。model_copy 不改原 config。
    """
    if not config.judge_model:
        return None
    return config.model_copy(update={
        "model": config.judge_model,
        "base_url": config.judge_base_url or config.base_url,
        "api_key": config.judge_api_key or config.api_key,
    })


def build_judge_completer(client, config):
    """构造 judge 专用 completer：配了 judge_model 则起独立 client（可指向独立端点/key），
    否则回退传入的主 client/主模型。用独立/更强模型当裁判可降低「自己给自己打高分」的偏差。
    judge 默认关闭思考模式——打分/判断无需思考链，省 token 与延迟。

    注意：build_completer 的 model_name 仅作计费标签，实际模型固化在 client 的 config 里，
    所以换裁判模型必须新建 client（而非仅传不同 model_name）。
    """
    jcfg = _judge_config(config)
    if jcfg is None:
        base = build_completer(client, config.model)
    else:
        from harness.llm.openai_compat import OpenAICompatibleClient
        from harness.reliability.retry import RetryingModelClient
        jclient = RetryingModelClient(
            OpenAICompatibleClient(jcfg),
            max_retries=config.max_retries, base_delay=config.retry_base_delay)
        base = build_completer(jclient, config.judge_model)

    async def complete(system_prompt: str, user_prompt: str) -> str:
        # 叠加在当前 extra_body 覆盖之上，只强制关思考（不动其他键），调用后还原
        from harness.llm.openai_compat import (
            get_extra_body_override, set_extra_body_override, reset_extra_body_override)
        token = set_extra_body_override(
            {**get_extra_body_override(), "enable_thinking": False})
        try:
            return await base(system_prompt, user_prompt)
        finally:
            reset_extra_body_override(token)

    return complete

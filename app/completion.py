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


def _alt_config(config, model: str, base_url: str, api_key: str):
    """按 <role>_* 覆盖返回该角色专用 config；model 为空则返回 None（回退主模型）。

    参照 embedding/rerank 的回退模式：base_url/api_key 为空时回退主端点，因此「只配
    <role>_model 换模型」和「配独立端点/key」都能工作。model_copy 不改原 config。
    """
    if not model:
        return None
    return config.model_copy(update={
        "model": model,
        "base_url": base_url or config.base_url,
        "api_key": api_key or config.api_key,
    })


def _build_alt_completer(client, config, model: str, base_url: str, api_key: str):
    """按角色配置产出 base completer：配了独立 model 就起独立 client，否则回退主 client。

    注意：build_completer 的 model_name 仅作计费标签，实际模型固化在 client 的 config 里，
    所以换模型必须新建 client（而非仅传不同 model_name）。
    """
    cfg = _alt_config(config, model, base_url, api_key)
    if cfg is None:
        return build_completer(client, config.model)
    from harness.llm.openai_compat import OpenAICompatibleClient
    from harness.reliability.retry import RetryingModelClient
    alt = RetryingModelClient(
        OpenAICompatibleClient(cfg),
        max_retries=config.max_retries, base_delay=config.retry_base_delay)
    return build_completer(alt, model)


def _with_thinking(base, enabled: bool):
    """包一层：本次调用显式指定 enable_thinking，叠加在当前 extra_body 覆盖之上、调用后还原。

    必须显式发：不发这个键时由模型服务端的默认决定（Qwen3 系默认开思考），
    对打分/摘要这类机械活等于白烧 token 与延迟。
    """
    async def complete(system_prompt: str, user_prompt: str) -> str:
        from harness.llm.openai_compat import (
            get_extra_body_override, set_extra_body_override, reset_extra_body_override)
        token = set_extra_body_override(
            {**get_extra_body_override(), "enable_thinking": enabled})
        try:
            return await base(system_prompt, user_prompt)
        finally:
            reset_extra_body_override(token)

    return complete


def build_judge_completer(client, config):
    """构造 judge 专用 completer：配了 judge_model 则起独立 client（可指向独立端点/key），
    否则回退传入的主 client/主模型。用独立/更强模型当裁判可降低「自己给自己打高分」的偏差。
    judge 恒定关闭思考模式——打分/判断无需思考链，省 token 与延迟。
    """
    base = _build_alt_completer(
        client, config, config.judge_model, config.judge_base_url, config.judge_api_key)
    return _with_thinking(base, False)


def build_summary_completer(client, config):
    """构造 L2 摘要专用 completer：配了 summary_model 则起独立 client（可指向独立端点/key），
    否则回退传入的主 client/主模型。摘要是把挤出窗口的历史压成短文的机械活，用便宜小模型足矣。

    思考模式由 summary_enable_thinking 决定（默认关）。它必须自己指定：摘要跑在上下文组装
    阶段，早于 gen() 里那句按请求的 set_extra_body_override，够不着聊天页的思考开关。
    """
    base = _build_alt_completer(
        client, config, config.summary_model, config.summary_base_url, config.summary_api_key)
    return _with_thinking(base, bool(config.summary_enable_thinking))

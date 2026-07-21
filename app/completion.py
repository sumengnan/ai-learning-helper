# app/completion.py
from __future__ import annotations

from harness.context.manager import ContextManager
from harness.events import ModelUsage, ReasoningDelta, RunError, RunFinished
from harness.loop.agent_loop import AgentLoop
from harness.tools.base import ToolRegistry

from .orchestration.usage_ctx import record_reasoning, record_usage
from .today import with_today


def build_completer(client, model_name: str, *, max_prompt_tokens: int = 0,
                    count_model: str | None = None):
    """返回 async (system_prompt, user_prompt) -> str：跑一轮无工具 AgentLoop，取最终文本。

    所有单轮模型调用（规划、校验、交付门 grounding/judge、出题判分、起标题、题目抽取…）
    都经由这里，故当前日期在此统一注入——见 with_today。

    复用 harness 的重试/预算/OTel 封装；不给 harness 加任何能力。
    max_prompt_tokens>0 时给上下文包一层 ClampedContextManager，对总输入按 (count_model, 上限)
    确定性硬裁——单轮 completer（如 judge）只有 system+一条 user，会对巨型 user 做中段截断（保头保尾）。
    默认 0=不裁（零开销、行为不变）。
    """
    cap = int(max_prompt_tokens or 0)
    cmodel = count_model or model_name

    async def complete(system_prompt: str, user_prompt: str) -> str:
        ctx = ContextManager(with_today(system_prompt))
        if cap > 0:
            from harness.context.clamp import ClampedContextManager
            ctx = ClampedContextManager(ctx, cmodel, cap)
        loop = AgentLoop(client=client, registry=ToolRegistry(),
                         context=ctx, max_steps=1, model_name=model_name)
        final = ""
        async for ev in loop.run(user_prompt):
            if isinstance(ev, RunFinished):
                final = ev.message.content or ""
            elif isinstance(ev, ModelUsage):   # 记进编排器用量累加器（非编排器路径 no-op）
                record_usage(ev.usage, ev.cost_usd, ev.model,
                             ev.latency_ms, ev.attempts)
            elif isinstance(ev, ReasoningDelta):   # 思考记进 sink（仅 planner 调用期挂 sink；否则 no-op）
                record_reasoning(ev.text)
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


def _build_alt_completer(client, config, model: str, base_url: str, api_key: str,
                         *, max_prompt_tokens: int = 0):
    """按角色配置产出 base completer：配了独立 model 就起独立 client，否则回退主 client。

    注意：build_completer 的 model_name 仅作计费标签，实际模型固化在 client 的 config 里，
    所以换模型必须新建 client（而非仅传不同 model_name）。
    max_prompt_tokens>0 时给该角色的输入加确定性硬上限（按其实际模型的分词器计数）。
    """
    cfg = _alt_config(config, model, base_url, api_key)
    if cfg is None:
        return build_completer(client, config.model, max_prompt_tokens=max_prompt_tokens)
    from harness.llm.openai_compat import OpenAICompatibleClient
    from harness.reliability.retry import RetryingModelClient
    alt = RetryingModelClient(
        OpenAICompatibleClient(cfg),
        max_retries=config.max_retries, base_delay=config.retry_base_delay)
    return build_completer(alt, model, max_prompt_tokens=max_prompt_tokens)


def build_fast_client(client, config):
    """返回 (client, model_name)：配了 fast_model 就起指向快速模型的独立 client，否则回退主 client/主模型。

    供需要「带工具的快速档 AgentLoop」的场景用（如编排器执行步）——那里要的是完整工具循环，
    不是 build_fast_completer 的单轮无工具 completer，故单独提供 client 版。
    """
    cfg = _alt_config(config, config.fast_model, config.fast_base_url, config.fast_api_key)
    if cfg is None:
        return client, config.model
    from harness.llm.openai_compat import OpenAICompatibleClient
    from harness.reliability.retry import RetryingModelClient
    alt = RetryingModelClient(
        OpenAICompatibleClient(cfg),
        max_retries=config.max_retries, base_delay=config.retry_base_delay)
    return alt, config.fast_model


def _with_thinking(base, enabled: bool):
    """包一层：本次调用显式指定思考意图 enable_thinking，叠加在当前 extra_body 覆盖之上、
    调用后还原。enable_thinking 是厂商中立意图，发送前由 openai_compat._adapt_thinking 按端点
    翻译成各家参数（Qwen→enable_thinking、DeepSeek→thinking={"type": ...}）。

    必须显式发：不发这个键时由服务端默认决定（Qwen3 默认开思考、DeepSeek deepseek-v4-pro
    也默认开），对打分/校验/摘要这类机械活等于白烧 token 与延迟。
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
        client, config, config.judge_model, config.judge_base_url, config.judge_api_key,
        max_prompt_tokens=getattr(config, "context_max_prompt_tokens_judge", 0))
    return _with_thinking(base, False)


def build_check_completer(client, config):
    """构造交付门事实核对（grounding）用的 completer：主模型 + 关思考。

    留在主模型而非 judge 档：grounding 是拿答案对着检索到的原文核对有无依据，不是自评
    打分，没有「给自己打高分」的偏差可言，不必占用（可能更贵的）裁判模型。
    但思考必须跟 judge 一样显式关掉——它同为校验判断题，不需要推理链。

    此前它用的是不带任何覆盖的 build_completer，实际继承了聊天页那个「思考模式」开关
    （chat.py 在 gen() 里设了 enable_thinking 且从不 reset）——于是同一个 AnswerVerifier
    里，打分恒关思考、grounding 却跟着用户开关走，两个校验动作行为不一致。
    """
    return _with_thinking(build_completer(client, config.model), False)


def build_fast_completer(client, config):
    """构造「快速模型」档 completer：配了 fast_model 则起独立 client（可指向独立端点/key），
    否则回退传入的主 client/主模型。供压缩/命名/解析这类机械活用——当前是 L2 摘要、
    对话自动命名、记忆写入的事实提炼、记忆整合蒸馏、HyDE 改写、导入题目解析。

    思考链恒关（同 judge 档，不给配置）：机械活开思考纯烧 token 与延迟。且必须自己显式
    关——这些都是旁路调用，够不着聊天页那个思考开关（它只作用于本轮任务的模型调用），
    不表态就由服务端默认决定。这与「换不换模型」无关：没配 fast_model、回退主模型时
    同样关，不配独立模型的人也该省下这份开销。
    """
    base = _build_alt_completer(
        client, config, config.fast_model, config.fast_base_url, config.fast_api_key)
    return _with_thinking(base, False)

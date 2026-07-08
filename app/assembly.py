# app/assembly.py
from __future__ import annotations

from dataclasses import dataclass

from harness.llm.openai_compat import OpenAICompatibleClient
from harness.persistence.checkpoint import CheckpointStore
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink
from harness.reliability.retry import RetryingModelClient
from harness.tools.base import ToolRegistry
from harness.tools.builtins.calculator import CalculatorTool
from harness.tools.builtins.http_tool import HttpRequestTool


@dataclass
class Harness:
    client: object
    registry: ToolRegistry
    checkpoint_store: CheckpointStore
    trajectory_store: TrajectoryStore
    sink: TrajectorySink
    system_prompt: str


def build_harness(config) -> Harness:
    client = RetryingModelClient(
        OpenAICompatibleClient(config),
        max_retries=config.max_retries, base_delay=config.retry_base_delay)

    reg = ToolRegistry()
    pool: dict = {}   # 工具池：name -> Tool，供 dispatch 组装子 agent registry

    def _reg(tool):
        reg.register(tool)
        pool[tool.name] = tool

    _reg(CalculatorTool())
    _reg(HttpRequestTool(
        config.http_allowed_domains, config.http_block_private, config.http_timeout,
        config.http_max_response_bytes, config.http_max_redirects))

    # 记忆（有 api_key 即可注册；知识库为空时检索返回空，不报错）
    if config.api_key or config.embedding_api_key:
        from harness.memory.embeddings import OpenAICompatibleEmbeddingClient
        from harness.memory.store import MemoryStore
        from harness.memory.memory import Memory
        from harness.memory.episodic import EpisodicMemory
        from harness.tools.builtins.memory_search import SearchMemoryTool
        from harness.tools.builtins.memory_write import RememberTool
        from harness.tools.builtins.episode_tools import RecallEpisodesTool
        embedder = OpenAICompatibleEmbeddingClient(
            config.embedding_base_url, config.embedding_api_key or config.api_key,
            config.embedding_model, config.embedding_dimension)
        mem = Memory(MemoryStore(config.memory_db_path, config.embedding_dimension), embedder,
                     config.chunk_size, config.chunk_overlap)
        _reg(SearchMemoryTool(mem, default_k=config.search_top_k))
        _reg(RememberTool(mem))
        _reg(RecallEpisodesTool(EpisodicMemory(mem), default_k=config.episode_recall_k))

    if config.enable_browser:
        from harness.browser.factory import build_browser
        from harness.tools.builtins.browse_tool import BrowseTool
        _reg(BrowseTool(
            build_browser(config), config.http_allowed_domains, config.http_block_private,
            config.browser_nav_timeout, config.browser_wait_until, config.browser_output_max_chars))

    if config.enable_sandbox and config.sandbox_docker_host:
        from harness.sandbox.factory import build_sandbox
        from harness.tools.builtins.fs_tools import WriteFileTool, ReadFileTool, ListFilesTool
        from harness.tools.builtins.shell_tool import RunShellTool
        from harness.tools.builtins.code_tool import RunPythonTool
        sb = build_sandbox(config)
        _reg(WriteFileTool(sb))
        _reg(ReadFileTool(sb, config.sandbox_output_max_chars))
        _reg(ListFilesTool(sb))
        _reg(RunShellTool(sb, config.sandbox_exec_timeout, config.sandbox_output_max_chars))
        _reg(RunPythonTool(sb, config.sandbox_exec_timeout, config.sandbox_output_max_chars))

    if config.enable_dispatch:
        from harness.orchestration.spec import AgentSpec, AgentRoster
        from harness.orchestration.dispatch import DispatchTool
        specs = []
        research_tools = [n for n in ["search_memory", "http_request", "browse"] if n in pool]
        if research_tools:
            specs.append(AgentSpec("researcher", "擅长检索与联网查资料",
                                   "你是研究员，用工具检索知识库/联网/抓网页查资料并给出结论。", research_tools))
        coder_tools = [n for n in ["run_python", "run_shell", "write_file", "read_file", "list_files"] if n in pool]
        if coder_tools:
            specs.append(AgentSpec("coder", "擅长写并运行代码",
                                   "你是程序员，写代码并在沙箱运行验证后给出结果。", coder_tools))
        if specs:
            # budget=None：子 agent 在此装配下不单独限预算，主 loop 仍有预算——App-1 可接受
            reg.register(DispatchTool(
                AgentRoster(specs), pool, client, budget=None, tracer=None, depth=0,
                max_depth=config.max_dispatch_depth, sub_max_steps=config.sub_agent_max_steps,
                model_name=config.model, price_map=config.price_map))

    traj = TrajectoryStore(config.persistence_db_path)
    return Harness(
        client=client, registry=reg,
        checkpoint_store=CheckpointStore(config.persistence_db_path),
        trajectory_store=traj, sink=TrajectorySink(traj),
        system_prompt=config.app_system_prompt)

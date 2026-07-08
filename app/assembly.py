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
    reg.register(CalculatorTool())
    reg.register(HttpRequestTool(
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
        reg.register(SearchMemoryTool(mem, default_k=config.search_top_k))
        reg.register(RememberTool(mem))
        reg.register(RecallEpisodesTool(EpisodicMemory(mem), default_k=config.episode_recall_k))

    if config.enable_browser:
        from harness.browser.factory import build_browser
        from harness.tools.builtins.browse_tool import BrowseTool
        reg.register(BrowseTool(
            build_browser(config), config.http_allowed_domains, config.http_block_private,
            config.browser_nav_timeout, config.browser_wait_until, config.browser_output_max_chars))

    if config.enable_sandbox and config.sandbox_docker_host:
        from harness.sandbox.factory import build_sandbox
        from harness.tools.builtins.fs_tools import WriteFileTool, ReadFileTool, ListFilesTool
        from harness.tools.builtins.shell_tool import RunShellTool
        from harness.tools.builtins.code_tool import RunPythonTool
        sb = build_sandbox(config)
        reg.register(WriteFileTool(sb))
        reg.register(ReadFileTool(sb, config.sandbox_output_max_chars))
        reg.register(ListFilesTool(sb))
        reg.register(RunShellTool(sb, config.sandbox_exec_timeout, config.sandbox_output_max_chars))
        reg.register(RunPythonTool(sb, config.sandbox_exec_timeout, config.sandbox_output_max_chars))

    traj = TrajectoryStore(config.persistence_db_path)
    return Harness(
        client=client, registry=reg,
        checkpoint_store=CheckpointStore(config.persistence_db_path),
        trajectory_store=traj, sink=TrajectorySink(traj),
        system_prompt=config.app_system_prompt)

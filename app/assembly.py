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

from .tools.plan_tool import UpdatePlanTool, PLAN_SYSTEM_GUIDANCE


@dataclass
class Harness:
    client: object
    registry: ToolRegistry
    checkpoint_store: CheckpointStore
    trajectory_store: TrajectoryStore
    sink: TrajectorySink
    system_prompt: str
    memory: object | None = None
    memory_store: object | None = None
    download_store: object | None = None
    skill_registry: object | None = None
    sandbox: object | None = None            # 绑进工具的会话级沙箱代理（SandboxProxy）
    sandbox_manager: object | None = None    # 会话级容器生命周期管理（销毁/关停/清扫）
    mcp_manager: object | None = None        # MCP 客户端管理器（startup 期连接、注册远程工具）


def build_harness(config) -> Harness:
    client = RetryingModelClient(
        OpenAICompatibleClient(config),
        max_retries=config.max_retries, base_delay=config.retry_base_delay)

    reg = ToolRegistry()
    pool: dict = {}   # 工具池：name -> Tool，供 dispatch 组装子 agent registry
    memory = None
    memory_store = None

    def _reg(tool):
        reg.register(tool)
        pool[tool.name] = tool

    _reg(CalculatorTool())
    _reg(UpdatePlanTool())

    # 沙箱（若启用）：会话级隔离。绑进工具的是 SandboxProxy（按当前会话解析真实容器），
    # 真实容器由 SandboxManager 按 conv_id 惰性建/缓存/销毁。
    sandbox = None
    sandbox_manager = None
    if config.enable_sandbox and config.sandbox_docker_host:
        from .sandbox_manager import SandboxManager, SandboxProxy
        sandbox_manager = SandboxManager(config)
        sandbox = SandboxProxy(sandbox_manager)

    # http_request：有沙箱时在容器内用 curl 出网，否则回退到宿主 httpx
    if sandbox is not None:
        from harness.tools.builtins.sandbox_http_tool import SandboxedHttpRequestTool
        _reg(SandboxedHttpRequestTool(
            sandbox, config.http_allowed_domains, config.http_block_private,
            config.http_timeout, config.http_max_response_bytes, config.http_max_redirects))
    else:
        _reg(HttpRequestTool(
            config.http_allowed_domains, config.http_block_private, config.http_timeout,
            config.http_max_response_bytes, config.http_max_redirects))

    # 记忆（有 api_key 即可注册；知识库为空时检索返回空，不报错）
    if config.api_key or config.embedding_api_key:
        from harness.memory.embeddings import OpenAICompatibleEmbeddingClient
        from harness.memory.sqlite_backend import SqliteVecBackend
        from harness.memory.memory import Memory
        from harness.memory.episodic import EpisodicMemory
        from harness.tools.builtins.memory_search import SearchMemoryTool
        from harness.tools.builtins.memory_write import RememberTool
        from harness.tools.builtins.episode_tools import RecallEpisodesTool
        embedder = OpenAICompatibleEmbeddingClient(
            config.embedding_base_url, config.embedding_api_key or config.api_key,
            config.embedding_model, config.embedding_dimension)
        mem_store = SqliteVecBackend(config.memory_db_path, config.embedding_dimension)
        mem = Memory(mem_store, embedder, config.chunk_size, config.chunk_overlap)
        memory = mem
        memory_store = mem_store
        _reg(SearchMemoryTool(mem, default_k=config.search_top_k))
        _reg(RememberTool(mem))
        _reg(RecallEpisodesTool(EpisodicMemory(mem), default_k=config.episode_recall_k))

    if config.enable_browser:
        from harness.browser.factory import build_browser
        from harness.tools.builtins.browse_tool import BrowseTool
        _reg(BrowseTool(
            build_browser(config, sandbox), config.http_allowed_domains, config.http_block_private,
            config.browser_nav_timeout, config.browser_wait_until, config.browser_output_max_chars,
            sandbox=sandbox))   # 有沙箱则 DNS 解析下沉到容器内（与 http_request 对称）

    if sandbox is not None:
        from harness.tools.builtins.fs_tools import WriteFileTool, ReadFileTool, ListFilesTool
        from harness.tools.builtins.shell_tool import RunShellTool
        from harness.tools.builtins.code_tool import RunPythonTool, RunNodeTool, RunJavaTool
        _reg(WriteFileTool(sandbox))
        _reg(ReadFileTool(sandbox, config.sandbox_output_max_chars))
        _reg(ListFilesTool(sandbox))
        _reg(RunShellTool(sandbox, config.sandbox_exec_timeout, config.sandbox_output_max_chars))
        _reg(RunPythonTool(sandbox, config.sandbox_exec_timeout, config.sandbox_output_max_chars))
        # 配了多镜像路由（sandbox_images）或语言/版本子沙箱（sandbox_lang_images）时暴露多语言代码工具
        if getattr(sandbox, "sandbox_for", None) is not None or config.sandbox_lang_images:
            _reg(RunNodeTool(sandbox, config.sandbox_exec_timeout, config.sandbox_output_max_chars))
            _reg(RunJavaTool(sandbox, config.sandbox_exec_timeout, config.sandbox_output_max_chars))

    if config.enable_dispatch:
        from harness.orchestration.roster_loader import load_roster
        from harness.orchestration.spec import AgentSpec, AgentRoster
        from harness.orchestration.dispatch import DispatchTool
        raw_specs, _warns = load_roster(config.agents_dir)
        specs = []
        for s in raw_specs:
            avail = [t for t in s.tool_names if t in pool]   # 缺失工具丢弃（优雅降级）
            if avail:                                        # 工具全不可用则跳过该 agent
                specs.append(AgentSpec(s.name, s.description, s.system_prompt, avail))
        if specs:
            # budget=None：子 agent 在此装配下不单独限预算，主 loop 仍有预算——App-1 可接受
            reg.register(DispatchTool(
                AgentRoster(specs), pool, client, budget=None, tracer=None, depth=0,
                max_depth=config.max_dispatch_depth, sub_max_steps=config.sub_agent_max_steps,
                model_name=config.model, price_map=config.price_map))

    # 技能（渐进式披露）：扫描技能目录，非空才注册三个工具
    skill_registry = None
    if config.enable_skills:
        from harness.skills.registry import SkillRegistry
        from harness.skills.tools import (
            LoadSkillTool, ReadSkillResourceTool, UnloadSkillTool)
        skill_registry = SkillRegistry(config.skills_dir, config.skill_resource_max_chars)
        if not skill_registry.is_empty():
            _reg(LoadSkillTool(skill_registry))
            _reg(UnloadSkillTool(skill_registry))
            _reg(ReadSkillResourceTool(skill_registry))
        else:
            skill_registry = None   # 无技能可加载，不必包装 context

    from .downloads import DownloadStore
    from .tools.save_download import SaveDownloadTool
    dstore = DownloadStore(config.downloads_dir, db_path=config.app_db_path)
    _reg(SaveDownloadTool(dstore, config.download_max_mb * 1024 * 1024))

    # MCP 客户端：此处只构造管理器（不连接——build_harness 是同步的）。
    # 实际连接与工具注册在 FastAPI startup 钩子里 await（见 app/main.py）。
    mcp_manager = None
    if getattr(config, "enable_mcp", False):
        from harness.mcp import MCPManager
        mcp_manager = MCPManager(config)

    traj = TrajectoryStore(config.persistence_db_path)
    return Harness(
        client=client, registry=reg,
        checkpoint_store=CheckpointStore(config.persistence_db_path),
        trajectory_store=traj, sink=TrajectorySink(traj),
        system_prompt=config.app_system_prompt + PLAN_SYSTEM_GUIDANCE,
        memory=memory, memory_store=memory_store, download_store=dstore,
        skill_registry=skill_registry, sandbox=sandbox, sandbox_manager=sandbox_manager,
        mcp_manager=mcp_manager)

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

from .search_guidance import SEARCH_SYSTEM_GUIDANCE
from .tools.plan_tool import UpdatePlanTool, PLAN_SYSTEM_GUIDANCE
from .tools.validating import ValidatingTool, relevance_check, web_content_check


# 执行子步的隐藏工具视图 HidingRegistry 已移至 app.orchestration.executor（供编排器与装配层共用）。


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
    memory_writer: object | None = None
    memory_maintainer: object | None = None
    download_store: object | None = None
    skill_registry: object | None = None
    sandbox: object | None = None            # 绑进工具的会话级沙箱代理（SandboxProxy）
    sandbox_manager: object | None = None    # 会话级容器生命周期管理（销毁/关停/清扫）
    mcp_manager: object | None = None        # MCP 客户端管理器（startup 期连接、注册远程工具）
    orchestrator: object | None = None       # 启用编排器时的 Plan-Execute-Reflect 控制器


def build_harness(config) -> Harness:
    client = RetryingModelClient(
        OpenAICompatibleClient(config),
        max_retries=config.max_retries, base_delay=config.retry_base_delay)

    reg = ToolRegistry()
    pool: dict = {}   # 工具池：name -> Tool，供 dispatch 组装子 agent registry
    memory = None
    memory_store = None
    memory_writer = None
    memory_maintainer = None

    def _reg(tool):
        reg.register(tool)
        pool[tool.name] = tool

    def _reg_exec(tool):
        # 代码/命令类：套每步校验（捕 ToolError 标记执行未通过），可整体关闭
        _reg(ValidatingTool(tool, exec_mode=True) if config.enable_step_check else tool)

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

    # http_request：有沙箱时在容器内用 curl 出网，否则回退到宿主 httpx。
    # 保留实例引用，稍后浏览器就绪时挂上「抓取失败/被防抓自动改用浏览器」的兜底。
    if sandbox is not None:
        from harness.tools.builtins.sandbox_http_tool import SandboxedHttpRequestTool
        http_tool = SandboxedHttpRequestTool(
            sandbox, config.http_allowed_domains, config.http_block_private,
            config.http_timeout, config.http_max_response_bytes, config.http_max_redirects,
            user_agent=config.http_user_agent)
    else:
        http_tool = HttpRequestTool(
            config.http_allowed_domains, config.http_block_private, config.http_timeout,
            config.http_max_response_bytes, config.http_max_redirects,
            user_agent=config.http_user_agent)
    # 联网抓取包 web_content_check：拦「抓取成功但抓到的是占位域名/空壳页」——
    # 模型编造的网址往往落在 example.com 这类真实存在且恒返回 200 的域名上，
    # 靠状态码和错误页判据是拦不住的。
    _reg(ValidatingTool(http_tool, web_content_check)
         if config.enable_step_check else http_tool)

    # 记忆（有 api_key 即可注册；知识库为空时检索返回空，不报错）
    if config.api_key or config.embedding_api_key:
        from harness.memory.embeddings import OpenAICompatibleEmbeddingClient
        from harness.memory.sqlite_backend import SqliteVecBackend
        from harness.memory.memory import Memory
        from harness.memory.episodic import EpisodicMemory
        from harness.tools.builtins.memory_search import (
            SearchKnowledgeTool, SearchMemoryTool)
        from harness.tools.builtins.memory_write import RememberTool
        from harness.tools.builtins.episode_tools import RecallEpisodesTool
        embedder = OpenAICompatibleEmbeddingClient(
            config.embedding_base_url, config.embedding_api_key or config.api_key,
            config.embedding_model, config.embedding_dimension)
        mem_store = SqliteVecBackend(config.memory_db_path, config.embedding_dimension)
        from harness.memory.reranker import HttpReranker, NoOpReranker
        from harness.memory.retriever import RetrievalConfig, Retriever
        # 精排：开关开且配了端点+模型才启用远程 rerank，否则维持 NoOp（零行为变更）。
        reranker = NoOpReranker()
        if config.enable_rerank and config.rerank_base_url and config.rerank_model:
            reranker = HttpReranker(
                config.rerank_base_url,
                config.rerank_api_key or config.embedding_api_key or config.api_key,
                config.rerank_model, style=config.rerank_style,
                timeout=config.rerank_timeout,
                top_n=config.rerank_top_n or None)
        _rcfg = RetrievalConfig(
            candidate_pool=config.retrieval_candidate_pool,
            w_relevance=config.retrieval_w_relevance,
            w_recency=config.retrieval_w_recency,
            w_importance=config.retrieval_w_importance,
            recency_half_life_days=config.retrieval_recency_half_life_days,
            use_keyword=config.retrieval_use_keyword,
            use_mmr=config.retrieval_use_mmr,
            mmr_lambda=config.retrieval_mmr_lambda,
            rrf_k=config.retrieval_rrf_k,
            use_entity_recall=config.retrieval_use_entity_recall,
            use_multi_query=config.retrieval_use_multi_query,
            use_hyde=config.retrieval_use_hyde,
            multi_query_n=config.retrieval_multi_query_n,
            query_plan_timeout_s=config.retrieval_query_plan_timeout_s,
            rerank_min_score=config.rerank_min_score)
        from app.completion import build_completer, build_fast_completer
        # 查询期召回增强的 LLM（三路默认关时不会被调用；开启才在检索时用）。
        # 走快速档：它卡在聊天首字的关键路径上、还带 2 秒超时，越快越好，思考链是找死。
        # 且必须自己表态——它既会被 build_manager 调（在 pump 外）、也会被 SearchMemoryTool
        # 调（在 pump 内），不显式声明就会「同一个 completer 两种行为」，取决于谁调它。
        _retriever = Retriever(mem_store, embedder, reranker, _rcfg,
                               complete=build_fast_completer(client, config))
        mem = Memory(mem_store, embedder, config.chunk_size, config.chunk_overlap,
                     retriever=_retriever, chunk_hard_max=config.chunk_hard_max)
        memory = mem
        memory_store = mem_store
        from harness.memory.maintainer import ConsolidationConfig, MemoryMaintainer
        # 整合的蒸馏走快速档：「把同主题的 N 条压成一条」与 L2 摘要同形，是机械活。
        # 破坏性的那步（set_superseded 作废原 episodic）由余弦聚类决定，不归模型判。
        memory_maintainer = MemoryMaintainer(
            mem_store, embedder, build_fast_completer(client, config),
            ConsolidationConfig(
                sim_threshold=config.consolidation_sim_threshold,
                min_cluster=config.consolidation_min_cluster,
                max_source=config.consolidation_max_source))
        if config.memory_write_extract:
            from harness.memory.writer import MemoryWriter
            _ttl_by_type = {
                "episodic": config.ttl_episodic_days * 86400,
                "semantic": config.ttl_semantic_days * 86400,
                "procedural": config.ttl_procedural_days * 86400,
            }
            from app.completion import build_fast_completer
            memory_writer = MemoryWriter(
                mem_store, embedder, _retriever,
                # 调和：判断题，判 REPLACE 会永久作废旧记忆 → 留主模型
                build_completer(client, config.model),
                # 提炼：机械活 → 快速档
                extract_complete=build_fast_completer(client, config),
                candidate_k=config.memory_write_candidate_k,
                ttl_by_type=_ttl_by_type)
        # 知识库检索包 relevance_check：空命中意味着「本轮没有可引用依据」，要驱动模型自纠正。
        _search_tool = SearchKnowledgeTool(mem, default_k=config.search_top_k)
        _reg(ValidatingTool(_search_tool, relevance_check)
             if config.enable_step_check else _search_tool)
        # 记忆检索不包校验：记忆为空是常态（新用户本就没记过什么），不是失败。
        _reg(SearchMemoryTool(mem, default_k=config.search_top_k))
        _reg(RememberTool(mem))
        _reg(RecallEpisodesTool(EpisodicMemory(mem), default_k=config.episode_recall_k))

    if config.enable_browser:
        from harness.browser.factory import build_browser
        from harness.tools.builtins.browse_tool import BrowseTool
        # 配了浏览器专用镜像 → 浏览器沙箱**全局共用一个**（跨会话），懒加载启动、复用，空闲 24h
        # 才销毁，避免每次重建 Chromium 容器；生命周期归 SandboxManager（关停时关闭）。基础镜像可保持轻量。
        browser_sub_acquire = None
        if sandbox is not None and config.browser_sandbox_image and sandbox_manager is not None:
            browser_sub_acquire = sandbox_manager.get_browser   # async ()->(box, True)
        browse_tool = BrowseTool(
            build_browser(config, sandbox, sub_acquire=browser_sub_acquire),
            config.http_allowed_domains, config.http_block_private,
            config.browser_nav_timeout, config.browser_wait_until, config.browser_output_max_chars,
            sandbox=sandbox)   # 有沙箱则 DNS 解析下沉到容器内（与 http_request 对称）
        _reg(ValidatingTool(browse_tool, web_content_check)
             if config.enable_step_check else browse_tool)
        # 自动兜底：http_request 抓取出错或疑似被防抓/需 JS 时，改用浏览器抓取同一 URL
        http_tool.set_browser_fallback(
            lambda url: browse_tool.run(BrowseTool.Params(url=url)))

    if sandbox is not None:
        from harness.tools.builtins.fs_tools import WriteFileTool, ReadFileTool, ListFilesTool
        from harness.tools.builtins.shell_tool import RunShellTool
        from harness.tools.builtins.code_tool import RunPythonTool, RunNodeTool, RunJavaTool
        _reg(WriteFileTool(sandbox))
        _reg(ReadFileTool(sandbox, config.sandbox_output_max_chars))
        _reg(ListFilesTool(sandbox))
        _reg_exec(RunShellTool(sandbox, config.sandbox_exec_timeout, config.sandbox_output_max_chars))
        _reg_exec(RunPythonTool(sandbox, config.sandbox_exec_timeout, config.sandbox_output_max_chars))
        # 配了多镜像路由（sandbox_images）或语言/版本子沙箱（sandbox_lang_images）时暴露多语言代码工具
        if getattr(sandbox, "sandbox_for", None) is not None or config.sandbox_lang_images:
            _reg_exec(RunNodeTool(sandbox, config.sandbox_exec_timeout, config.sandbox_output_max_chars))
            _reg_exec(RunJavaTool(sandbox, config.sandbox_exec_timeout, config.sandbox_output_max_chars))

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
                model_name=config.model, price_map=config.price_map,
                loop_detect_window=config.loop_detect_window))

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

    # 编排器（Plan-Execute-Reflect）：已成为唯一主流程，恒构建、无开关。chat 路由每请求把
    # 会话上下文（历史+全部指引+记忆）与每请求工具表（用户级工具）注入其 run()，故它可作为唯一
    # 主流程而不丢失多轮对话/附件/考试/引用/个性化。简单问答仍在其内部短路成单个 ReAct 直答，
    # 复杂任务才拆分并行执行，不额外增加简单场景开销。
    from app.completion import (build_completer, build_fast_completer, build_fast_client,
                                build_judge_completer)
    from app.orchestration.orchestrator import Orchestrator
    from app.orchestration.planner import Planner
    from app.orchestration.critic import Critic
    from app.orchestration.executor import Executor, HidingRegistry
    from harness.skills.matcher import SkillMatcher
    from app.sandbox_manager import sandbox_guide
    from harness.reliability.budget import BudgetTracker
    _plan_complete = build_completer(client, config.model)     # 规划用主模型（判断质量要求高）
    _fast_complete = build_fast_completer(client, config)      # triage + 单步 validate 用快速档（频繁，提速）
    # 终局 review 就是「裁判」这个角色，理应吃 judge 配置。此前它写死主模型，而 judge 只接在
    # 交付门 AnswerVerifier 上——编排器成为唯一主流程后那条分支永不进入（chat.py 里
    # `if orchestrator is not None` 在前短路），于是 HARNESS_JUDGE_MODEL 对用户看到的
    # 「结果校验」完全不起作用，且不报错。未配 judge_model 时 build_judge_completer 回退
    # 主 client/主模型，故对没配的人零行为变更。
    _review_complete = build_judge_completer(client, config)
    _exec_client, _exec_model = build_fast_client(client, config)   # 执行子步走快速档模型（占大头往返，提速）
    # 装配期回退用的执行子步工具视图（隐藏 update_plan）；实际运行时由 chat 路由传入每请求 registry 覆盖。
    _exec_reg = HidingRegistry(reg, {"update_plan"})
    orchestrator = Orchestrator(
        client=client, registry=reg, model=config.model,
        planner=Planner(_plan_complete, max_retries=config.orchestrator_planner_max_retries),
        critic=Critic(_review_complete, validate_complete=_fast_complete),
        executor=Executor(_exec_client, _exec_reg, config.app_system_prompt, _exec_model,
                          max_steps=config.orchestrator_step_max_steps,
                          loop_detect_window=config.loop_detect_window,
                          disable_thinking=config.orchestrator_step_disable_thinking,
                          # 有沙箱才按配置预渲染指引（工作目录/镜像/联网）；无沙箱这些工具没注册，提了反误导
                          sandbox_guide_text=(sandbox_guide(config)
                                              if sandbox is not None else "")),
        fast_complete=_fast_complete,
        # 简单直答走快速档模型/端点（省钱提速）；未配 fast_model 时 _exec_* 即回退主 client/主模型
        fast_client=_exec_client, fast_model=_exec_model,
        # 简单直答的上下文按快速模型口径再收一道（0=不裁）
        fast_max_prompt_tokens=config.context_max_prompt_tokens_fast,
        # 每次 run 新建独立预算封顶时长/token（超限带现有成果收尾）；单例并发安全
        budget_factory=lambda: BudgetTracker(config.max_tokens_budget, config.max_wall_seconds),
        max_step_retry=config.orchestrator_max_step_retry,
        max_replan=config.orchestrator_max_replan,
        # 技能路由：有技能时按触发词匹配、命中剧本注入 planner/直答（无技能则 None，零行为变更）
        skill_matcher=SkillMatcher(skill_registry) if skill_registry is not None else None)

    traj = TrajectoryStore(config.persistence_db_path)
    return Harness(
        client=client, registry=reg,
        checkpoint_store=CheckpointStore(config.persistence_db_path),
        trajectory_store=traj, sink=TrajectorySink(traj),
        system_prompt=(config.app_system_prompt + PLAN_SYSTEM_GUIDANCE
                       + SEARCH_SYSTEM_GUIDANCE),
        memory=memory, memory_store=memory_store, memory_writer=memory_writer,
        memory_maintainer=memory_maintainer,
        download_store=dstore,
        skill_registry=skill_registry, sandbox=sandbox, sandbox_manager=sandbox_manager,
        mcp_manager=mcp_manager, orchestrator=orchestrator)

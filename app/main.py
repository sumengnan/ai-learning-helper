# app/main.py
from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.attachments import make_attachments_router
from .api.auth import make_auth_router
from .api.chat import make_chat_router
from .api.conversations import make_conversations_router
from .api.documents import make_documents_router
from .api.downloads import make_downloads_router
from .api.profile import make_profile_router
from .api.questions import make_questions_router
from .api.stats import make_stats_router
from .api.version import make_version_router
from .api.wrong_answers import make_wrong_answers_router
from harness.telemetry.tracer import setup_telemetry

from .assembly import build_harness
from .attachments import AttachmentStore
from .auth import AuthService, UserStore
from .completion import build_completer, build_fast_completer
from .config import AppConfig, load_env_file
from .conversations import ConversationStore
from .db import migrate, open_db
from .url_blocklist import UrlBlockStore
from .documents import DocumentStore
from .exam_session import ExamSessionStore
from .profile import ProfileStore
from .knowledge import KnowledgeService
from .logging_setup import configure_logging
from .question_import import QuestionImporter
from .questions import QuestionStore
from .quiz_service import QuizService
from .wrong_answers import WrongAnswerStore

_DEFAULT_SECRET = "dev-insecure-secret-change-me"


def create_app(config: AppConfig | None = None, harness=None, store=None, doc_store=None,
               question_store=None, exam_store=None, wrong_store=None,
               quiz_service=None, user_store=None, verifier=None,
               attachment_store=None, stats_service=None, question_importer=None,
               profile_store=None, exam_session_store=None,
               url_block_store=None) -> FastAPI:
    # exam_store 参数保留仅为向后兼容（模拟考试已迁入聊天工具，不再有独立考试端点）
    if config is None:
        # 生产路径（python -m app → uvicorn factory，不传 config）：先把 .env 补进
        # os.environ，否则 mcp/mcp_servers.json 里的 ${VAR} 解析不出来——pydantic-settings
        # 只填配置对象、不写环境。调用方自带 config（测试/嵌入式）时不碰 os.environ。
        injected = load_env_file(AppConfig.model_config.get("env_file") or ".env")
        if injected:
            logging.getLogger("app").info(
                "从 .env 补入 %d 个环境变量：%s", len(injected), "、".join(injected))
        config = AppConfig()
    configure_logging()   # 幂等：确保测试/嵌入式启动也有可见日志
    # OTel：otel_enabled=False（默认）时是空操作，tracer 保持 no-op、零开销。
    # 不装则 harness/app 里所有插桩都白写，故在此唯一入口装配。
    setup_telemetry(config)
    harness = harness if harness is not None else build_harness(config)

    # 应用领域各 Store 共享同一个数据库连接（单文件 app.db）；仅在需要时创建，
    # 避免测试注入全部 Store 时产生多余的 app.db 副作用。
    need_db = any(s is None for s in (store, doc_store, question_store, wrong_store,
                                      user_store, attachment_store, profile_store,
                                      exam_session_store))
    app_conn = None
    if need_db:
        app_conn = open_db(config.app_db_path)
        migrate(app_conn)

    store = store if store is not None else ConversationStore(conn=app_conn)
    doc_store = doc_store if doc_store is not None else DocumentStore(conn=app_conn)
    question_store = question_store if question_store is not None else QuestionStore(conn=app_conn)
    wrong_store = wrong_store if wrong_store is not None else WrongAnswerStore(conn=app_conn)
    exam_session_store = (exam_session_store if exam_session_store is not None
                          else ExamSessionStore(conn=app_conn))
    attachment_store = (attachment_store if attachment_store is not None
                        else AttachmentStore(config.attachments_dir, conn=app_conn))
    profile_store = profile_store if profile_store is not None else ProfileStore(conn=app_conn)
    # 抓取失败网址登记（全局共享，不分用户）。app_conn 为 None 说明调用方注入了全部 Store
    # （测试路径），此时不自建库、guard 退化为直通——与其它 Store 的「不产生多余 app.db」一致。
    if url_block_store is None and config.enable_url_blocklist and app_conn is not None:
        url_block_store = UrlBlockStore(conn=app_conn)

    has_mem = (getattr(harness, "memory", None) is not None
               and getattr(harness, "memory_store", None) is not None)
    service = KnowledgeService(harness.memory, harness.memory_store, doc_store) if has_mem else None
    if quiz_service is None and has_mem:
        completer = build_completer(harness.client, config.model)
        quiz_service = QuizService(harness.memory, question_store, completer,
                                   retrieve_k=config.quiz_retrieve_k,
                                   short_pass_score=config.short_pass_score)
    if question_importer is None:
        # 走快速档：把粘贴/上传的文本解析成题目，是纯抽取，与记忆写入的 _extract 同形。
        # 它是独立接口、够不着聊天页那个思考开关，不自己表态就一路跟着服务端默认思考。
        question_importer = QuestionImporter(
            build_fast_completer(harness.client, config), question_store,
            chunk_chars=config.import_chunk_chars,
            max_concurrency=config.import_max_concurrency)

    user_store = user_store if user_store is not None else UserStore(conn=app_conn)
    secret = os.environ.get("AUTH_SECRET") or config.auth_secret
    if secret == _DEFAULT_SECRET:
        logging.getLogger("app").warning(
            "使用默认 AUTH_SECRET，生产环境请设置 AUTH_SECRET 环境变量")
    auth = AuthService(user_store, secret)

    app = FastAPI(title="AI 学习助手")
    app.state.auth = auth
    app.add_middleware(
        CORSMiddleware, allow_origins=config.cors_origins,
        allow_methods=["*"], allow_headers=["*"],
        expose_headers=["X-Refresh-Token", "X-Run-Id"])
    # 回答交付前校验门（开关开时装配；测试可注入 verifier）：用单发 completer 做
    # grounding/judge，代码块在会话沙箱实跑。
    from .completion import build_check_completer, build_judge_completer
    if verifier is None and config.enable_answer_gate:
        from .verify import AnswerVerifier
        # grounding 用核对档（主模型 + 关思考）、judge 用独立 completer（可指向独立端点/
        # 模型，降低自评打高分偏差）。两者同为校验动作，都不带思考链。
        verifier = AnswerVerifier(build_check_completer(harness.client, config), config,
                                  judge_complete=build_judge_completer(harness.client, config))
    # 轨迹 judge（交付前一次性回看整轨迹分层打分）：与 answer gate 独立，可单独开
    trajectory_judge = None
    if config.enable_trajectory_judge:
        from .verify import TrajectoryJudge
        trajectory_judge = TrajectoryJudge(
            build_judge_completer(harness.client, config), config)
    # 断点续传：进程内运行管理器（后台任务 + 内存事件总线），供 /api/chat 起后台生成、
    # attach 刷新接回。启动时对账残留的 streaming 消息（上次进程重启丢了在途任务）。
    from .run_manager import RunManager
    run_manager = RunManager()
    app.state.run_manager = run_manager
    try:
        n = store.reconcile_streaming()
        if n:
            logging.getLogger("app").info("启动对账：%d 条残留生成中消息标为中断", n)
    except Exception:  # 对账失败不应阻断启动
        pass

    @app.on_event("shutdown")
    async def _close_runs() -> None:
        await run_manager.close()

    app.include_router(make_version_router())
    app.include_router(make_auth_router(auth, require_captcha=config.require_captcha))
    app.include_router(make_conversations_router(store, harness, attachment_store, config))
    app.include_router(make_chat_router(harness, store, config,
                                        question_store=question_store, wrong_store=wrong_store,
                                        verifier=verifier, attachment_store=attachment_store,
                                        run_manager=run_manager, knowledge_service=service,
                                        quiz_service=quiz_service, profile_store=profile_store,
                                        trajectory_judge=trajectory_judge,
                                        exam_session_store=exam_session_store,
                                        url_block_store=url_block_store))
    app.include_router(make_documents_router(service, doc_store, config))
    app.include_router(make_attachments_router(attachment_store, store, config))

    dstore = getattr(harness, "download_store", None)
    if dstore is not None:
        app.include_router(make_downloads_router(dstore))

    app.include_router(make_questions_router(question_store, config, question_importer,
                                             wrong_store=wrong_store))
    app.include_router(make_wrong_answers_router(wrong_store))
    app.include_router(make_profile_router(profile_store))

    # 首页概览统计：聚合 harness 运行轨迹 + 应用业务数据。用独立只读连接（跨线程安全），
    # 复用请求期已建的 app_conn（若存在），memory 库缺失时降级为空。
    if stats_service is None:
        import sqlite3

        from .stats import StatsService
        traj_conn = sqlite3.connect(config.persistence_db_path, check_same_thread=False)
        stats_app_conn = app_conn if app_conn is not None else open_db(config.app_db_path)
        try:
            mem_conn = sqlite3.connect(config.memory_db_path, check_same_thread=False)
        except sqlite3.Error:
            mem_conn = None
        stats_service = StatsService(trajectory_conn=traj_conn, app_conn=stats_app_conn,
                                     memory_conn=mem_conn,
                                     memory_store=getattr(harness, "memory_store", None),
                                     price_tiers=config.model_price_tiers,
                                     currency=config.price_currency)
    app.include_router(make_stats_router(stats_service))

    # 会话级沙箱：启动时清扫上次遗留的孤儿容器；关停时销毁全部会话容器。
    sandbox_manager = getattr(harness, "sandbox_manager", None)
    if sandbox_manager is not None:
        sandbox_manager.sweep_orphans()

        @app.on_event("shutdown")
        async def _close_sandboxes() -> None:
            await sandbox_manager.close_all()

    # MCP 客户端：startup 时连接 server 并把远程工具注册进全局 registry（请求期 _build_registry
    # 会全量复制，故自动进入每次对话）；关停时断开（防遗留 stdio 僵尸子进程）。连接失败只 warning，
    # 不影响 app 启动。
    mcp_manager = getattr(harness, "mcp_manager", None)
    if mcp_manager is not None:
        from .api.mcp import make_mcp_router
        app.include_router(make_mcp_router(harness))

        @app.on_event("startup")
        async def _start_mcp() -> None:
            await mcp_manager.start()
            for t in mcp_manager.tools():
                harness.registry.register(t)

        @app.on_event("shutdown")
        async def _close_mcp() -> None:
            await mcp_manager.close()

    if os.path.isdir("web/dist"):  # prod：托管前端静态产物
        from fastapi.responses import FileResponse

        # 静态资源（js/css/favicon 等）仍由 StaticFiles 提供。
        app.mount("/assets", StaticFiles(directory="web/dist/assets"), name="assets")

        # SPA 兜底：前端使用 BrowserRouter（history 模式），刷新 /login 等子路径时
        # 需返回入口 index.html 交给前端路由，否则会命中 404（{"detail":"Not Found"}）。
        # API 路由已在前面 include_router 注册，优先于此 catch-all 匹配。
        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str):  # noqa: ANN202
            candidate = os.path.join("web/dist", full_path)
            if full_path and os.path.isfile(candidate):
                return FileResponse(candidate)
            return FileResponse("web/dist/index.html")

    # 启动摘要：一眼看清本次以什么配置起来的（模型/端口/各能力开关）
    logging.getLogger("app").info(
        "应用就绪 model=%s addr=%s:%s memory=%s rerank=%s answer_gate=%s sandbox=%s "
        "recall(entity=%s multi_query=%s hyde=%s) context=%s",
        config.model, config.app_host, config.app_port,
        getattr(harness, "memory", None) is not None,
        config.enable_rerank, config.enable_answer_gate,
        getattr(harness, "sandbox", None) is not None,
        config.retrieval_use_entity_recall, config.retrieval_use_multi_query,
        config.retrieval_use_hyde, getattr(config, "context_strategy", "full"))
    return app

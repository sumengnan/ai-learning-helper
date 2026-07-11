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
from .api.questions import make_questions_router
from .api.stats import make_stats_router
from .api.wrong_answers import make_wrong_answers_router
from .assembly import build_harness
from .attachments import AttachmentStore
from .auth import AuthService, UserStore
from .completion import build_completer
from .config import AppConfig
from .conversations import ConversationStore
from .db import migrate, open_db
from .documents import DocumentStore
from .knowledge import KnowledgeService
from .questions import QuestionStore
from .quiz_service import QuizService
from .wrong_answers import WrongAnswerStore

_DEFAULT_SECRET = "dev-insecure-secret-change-me"


def create_app(config: AppConfig | None = None, harness=None, store=None, doc_store=None,
               question_store=None, exam_store=None, wrong_store=None,
               quiz_service=None, user_store=None, verifier=None,
               attachment_store=None, stats_service=None) -> FastAPI:
    # exam_store 参数保留仅为向后兼容（模拟考试已迁入聊天工具，不再有独立考试端点）
    config = config or AppConfig()
    harness = harness if harness is not None else build_harness(config)

    # 应用领域各 Store 共享同一个数据库连接（单文件 app.db）；仅在需要时创建，
    # 避免测试注入全部 Store 时产生多余的 app.db 副作用。
    need_db = any(s is None for s in (store, doc_store, question_store, wrong_store,
                                      user_store, attachment_store))
    app_conn = None
    if need_db:
        app_conn = open_db(config.app_db_path)
        migrate(app_conn)

    store = store if store is not None else ConversationStore(conn=app_conn)
    doc_store = doc_store if doc_store is not None else DocumentStore(conn=app_conn)
    question_store = question_store if question_store is not None else QuestionStore(conn=app_conn)
    wrong_store = wrong_store if wrong_store is not None else WrongAnswerStore(conn=app_conn)
    attachment_store = (attachment_store if attachment_store is not None
                        else AttachmentStore(config.attachments_dir, conn=app_conn))

    has_mem = (getattr(harness, "memory", None) is not None
               and getattr(harness, "memory_store", None) is not None)
    service = KnowledgeService(harness.memory, harness.memory_store, doc_store) if has_mem else None
    if quiz_service is None and has_mem:
        completer = build_completer(harness.client, config.model)
        quiz_service = QuizService(harness.memory, question_store, completer,
                                   retrieve_k=config.quiz_retrieve_k,
                                   short_pass_score=config.short_pass_score)

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
    if verifier is None and config.enable_answer_gate:
        from .verify import AnswerVerifier
        verifier = AnswerVerifier(build_completer(harness.client, config.model), config)
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

    app.include_router(make_auth_router(auth))
    app.include_router(make_conversations_router(store, harness, attachment_store))
    app.include_router(make_chat_router(harness, store, config,
                                        question_store=question_store, wrong_store=wrong_store,
                                        verifier=verifier, attachment_store=attachment_store,
                                        run_manager=run_manager))
    app.include_router(make_documents_router(service, doc_store, config))
    app.include_router(make_attachments_router(attachment_store, store, config))

    dstore = getattr(harness, "download_store", None)
    if dstore is not None:
        app.include_router(make_downloads_router(dstore))

    app.include_router(make_questions_router(quiz_service, question_store, config))
    app.include_router(make_wrong_answers_router(wrong_store))

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
                                     memory_conn=mem_conn)
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
        app.mount("/", StaticFiles(directory="web/dist", html=True), name="static")
    return app

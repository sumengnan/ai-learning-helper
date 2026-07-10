# app/main.py
from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.auth import make_auth_router
from .api.chat import make_chat_router
from .api.conversations import make_conversations_router
from .api.documents import make_documents_router
from .api.downloads import make_downloads_router
from .api.questions import make_questions_router
from .api.wrong_answers import make_wrong_answers_router
from .assembly import build_harness
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
               quiz_service=None, user_store=None) -> FastAPI:
    # exam_store 参数保留仅为向后兼容（模拟考试已迁入聊天工具，不再有独立考试端点）
    config = config or AppConfig()
    harness = harness if harness is not None else build_harness(config)

    # 应用领域各 Store 共享同一个数据库连接（单文件 app.db）；仅在需要时创建，
    # 避免测试注入全部 Store 时产生多余的 app.db 副作用。
    need_db = any(s is None for s in (store, doc_store, question_store, wrong_store, user_store))
    app_conn = None
    if need_db:
        app_conn = open_db(config.app_db_path)
        migrate(app_conn)

    store = store if store is not None else ConversationStore(conn=app_conn)
    doc_store = doc_store if doc_store is not None else DocumentStore(conn=app_conn)
    question_store = question_store if question_store is not None else QuestionStore(conn=app_conn)
    wrong_store = wrong_store if wrong_store is not None else WrongAnswerStore(conn=app_conn)

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
        allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Refresh-Token"])
    app.include_router(make_auth_router(auth))
    app.include_router(make_conversations_router(store, harness))
    app.include_router(make_chat_router(harness, store, config,
                                        question_store=question_store, wrong_store=wrong_store))
    app.include_router(make_documents_router(service, doc_store, config))

    dstore = getattr(harness, "download_store", None)
    if dstore is not None:
        app.include_router(make_downloads_router(dstore))

    app.include_router(make_questions_router(quiz_service, question_store, config))
    app.include_router(make_wrong_answers_router(wrong_store))

    # 会话级沙箱：启动时清扫上次遗留的孤儿容器；关停时销毁全部会话容器。
    sandbox_manager = getattr(harness, "sandbox_manager", None)
    if sandbox_manager is not None:
        sandbox_manager.sweep_orphans()

        @app.on_event("shutdown")
        async def _close_sandboxes() -> None:
            await sandbox_manager.close_all()

    if os.path.isdir("web/dist"):  # prod：托管前端静态产物
        app.mount("/", StaticFiles(directory="web/dist", html=True), name="static")
    return app

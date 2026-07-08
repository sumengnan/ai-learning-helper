# app/main.py
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.chat import make_chat_router
from .api.conversations import make_conversations_router
from .api.documents import make_documents_router
from .assembly import build_harness
from .config import AppConfig
from .conversations import ConversationStore
from .documents import DocumentStore
from .knowledge import KnowledgeService


def create_app(config: AppConfig | None = None, harness=None, store=None, doc_store=None) -> FastAPI:
    config = config or AppConfig()
    harness = harness if harness is not None else build_harness(config)
    store = store if store is not None else ConversationStore(config.conversations_db_path)
    doc_store = doc_store if doc_store is not None else DocumentStore(config.documents_db_path)

    service = None
    if getattr(harness, "memory", None) is not None and getattr(harness, "memory_store", None) is not None:
        service = KnowledgeService(harness.memory, harness.memory_store, doc_store)

    app = FastAPI(title="AI 学习助手")
    app.add_middleware(
        CORSMiddleware, allow_origins=config.cors_origins,
        allow_methods=["*"], allow_headers=["*"])
    app.include_router(make_conversations_router(store))
    app.include_router(make_chat_router(harness, store, config))
    app.include_router(make_documents_router(service, doc_store, config))

    if os.path.isdir("web/dist"):  # prod：托管前端静态产物
        app.mount("/", StaticFiles(directory="web/dist", html=True), name="static")
    return app

"""FastAPI 入口。

API 层尽量保持薄，只做四件事：
1. 接收请求与参数校验；
2. 把请求转给 ChatService；
3. 返回结构化 JSON 或 SSE；
4. 作为学习入口，保留尽量清晰的接口命名。
"""

from __future__ import annotations

import base64

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .schemas import (
    CatalogResponse,
    CreateThreadRequest,
    CreateThreadResponse,
    KnowledgeSearchResponse,
    SendMessageRequest,
    ThreadState,
    ThreadSummary,
    UploadDocumentRequest,
)
from .services.chat_service import ChatService
from .services.knowledge_store import KnowledgeStore
from .services.llm_service import LLMService
from .services.thread_store import ThreadStore
from .settings import load_settings
from .skills.learning import build_skill_registry


settings = load_settings()
thread_store = ThreadStore(settings.sqlite_path)
knowledge_store = KnowledgeStore(settings.sqlite_path, settings.chroma_dir)
skill_registry = build_skill_registry(knowledge_store)
llm_service = LLMService(allow_mock_model=settings.allow_mock_model)
chat_service = ChatService(thread_store, knowledge_store, skill_registry, llm_service)

app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/catalog", response_model=CatalogResponse)
def get_catalog() -> CatalogResponse:
    return chat_service.get_catalog()


@app.get("/api/threads", response_model=list[ThreadSummary])
def list_threads() -> list[ThreadSummary]:
    return chat_service.list_threads()


@app.post("/api/threads", response_model=CreateThreadResponse)
def create_thread(request: CreateThreadRequest) -> CreateThreadResponse:
    return chat_service.create_thread(request)


@app.get("/api/threads/{thread_id}", response_model=ThreadState)
def get_thread(thread_id: str) -> ThreadState:
    return chat_service.get_thread(thread_id)


@app.post("/api/threads/{thread_id}/messages")
def post_message(thread_id: str, request: SendMessageRequest) -> StreamingResponse:
    return StreamingResponse(chat_service.stream_message(thread_id, request), media_type="text/event-stream")


@app.post("/api/knowledge/documents")
def upload_document(request: UploadDocumentRequest):
    content = base64.b64decode(request.content_base64.encode("utf-8"))
    return chat_service.ingest_document(request.file_name or "untitled.txt", content)


@app.get("/api/knowledge/documents")
def list_documents():
    return chat_service.list_documents()


@app.get("/api/knowledge/search", response_model=KnowledgeSearchResponse)
def search_knowledge(query: str) -> KnowledgeSearchResponse:
    return chat_service.search_knowledge(query)

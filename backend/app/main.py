"""FastAPI 入口。"""

from __future__ import annotations

import base64

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .schemas import (
    AgentConfig,
    AgentRunResponse,
    CatalogResponse,
    CreateSupportIssueAgentRequest,
    CreateWatcherRequest,
    CreateAgentRequest,
    CreateThreadRequest,
    CreateThreadResponse,
    DirectoryUploadRequest,
    GitLabImportSettings,
    GitLabTreeImportRequest,
    GitLabTreeImportResponse,
    UpdateGitLabImportSettingsRequest,
    FeishuBitableFieldsRequest,
    FeishuBitableFieldsResponse,
    FeishuBitablePendingAnalysisRequest,
    FeishuBitablePendingAnalysisResponse,
    FeishuBitablePreviewRequest,
    FeishuBitablePreviewResponse,
    FeishuSettings,
    FeishuBitableValidationRequest,
    FeishuBitableValidationResponse,
    FeishuBitableWriteValidationRequest,
    FeishuBitableWriteValidationResponse,
    KnowledgeDocument,
    KnowledgeDeleteResponse,
    KnowledgeNodeCreateRequest,
    KnowledgeTreeNode,
    KnowledgeTreeNodeDetail,
    KnowledgeTreeResponse,
    MailSettings,
    MailTestRequest,
    MailTestResponse,
    ProviderConfig,
    ProviderTestResponse,
    RAGEmbeddingSettings,
    RetrievalQueryRequest,
    RetrievalResult,
    RunSupportIssueAgentRequest,
    RunWatcherRequest,
    RunAgentRequest,
    SendMessageRequest,
    SupportIssueAgentConfig,
    SupportIssueCaseCandidate,
    SupportIssueDigestRun,
    SupportIssueFeedbackSyncResponse,
    SupportIssueInsights,
    SupportIssueRun,
    ThreadState,
    ThreadSummary,
    UpdateSupportIssueCaseCandidateRequest,
    UpdateFeishuSettingsRequest,
    UpdateKnowledgeDocumentRequest,
    UpdateMailSettingsRequest,
    UpdateProviderRequest,
    UpdateRAGEmbeddingSettingsRequest,
    UpdateWorkNotifySettingsRequest,
    UpdateSupportIssueAgentRequest,
    UpdateAgentRequest,
    UpdateWatcherRequest,
    UploadDocumentRequest,
    WatcherAgentConfig,
    WatcherFetchTestRequest,
    WatcherFetchTestResponse,
    WatcherRun,
    WorkNotifySettings,
)
from .services.agent_store import AgentStore
from .services.chat_service import ChatService
from .services.feishu_service import FeishuService
from .services.feishu_settings_store import FeishuSettingsStore
from .services.gitlab_import_service import GitLabImportService
from .services.gitlab_settings_service import GitLabSettingsService
from .services.gitlab_settings_store import GitLabSettingsStore
from .services.knowledge_store import KnowledgeStore
from .services.llm_service import LLMService
from .services.mail_service import MailService
from .services.mail_settings_store import MailSettingsStore
from .services.provider_store import ProviderStore
from .services.rag_embedding_settings_service import RAGEmbeddingSettingsService
from .services.rag_embedding_settings_store import RAGEmbeddingSettingsStore
from .services.support_issue_scheduler import SupportIssueScheduler
from .services.support_issue_service import SupportIssueService
from .services.support_issue_store import SupportIssueStore
from .services.thread_store import ThreadStore
from .services.watcher_scheduler import WatcherScheduler
from .services.watcher_service import WatcherService
from .services.watcher_store import WatcherStore
from .services.yonyou_contacts_search_service import YonyouContactsSearchService
from .services.work_notify_settings_service import WorkNotifySettingsService
from .services.work_notify_settings_store import WorkNotifySettingsStore
from .services.yonyou_work_notify_service import YonyouWorkNotifyService
from .settings import load_settings
from .skills.learning import build_skill_registry


settings = load_settings()
thread_store = ThreadStore(settings.sqlite_path)
agent_store = AgentStore(settings.sqlite_path)
provider_store = ProviderStore(settings.sqlite_path)
mail_store = MailSettingsStore(settings.sqlite_path)
feishu_store = FeishuSettingsStore(settings.sqlite_path)
gitlab_settings_store = GitLabSettingsStore(settings.sqlite_path)
rag_embedding_settings_store = RAGEmbeddingSettingsStore(settings.sqlite_path)
rag_embedding_settings_service = RAGEmbeddingSettingsService(rag_embedding_settings_store, settings)
knowledge_store = KnowledgeStore(
    settings.sqlite_path,
    settings.chroma_dir,
    provider_store=provider_store,
    settings=settings,
    rag_embedding_settings_service=rag_embedding_settings_service,
)
watcher_store = WatcherStore(settings.sqlite_path)
support_issue_store = SupportIssueStore(settings.sqlite_path)
work_notify_settings_store = WorkNotifySettingsStore(settings.sqlite_path)
llm_service = LLMService(provider_store=provider_store, allow_mock_model=settings.allow_mock_model)
gitlab_settings_service = GitLabSettingsService(gitlab_settings_store, settings)
gitlab_import_service = GitLabImportService(knowledge_store, gitlab_settings_service)
mail_service = MailService(mail_store=mail_store, app_settings=settings)
feishu_service = FeishuService(feishu_store=feishu_store)
yonyou_work_notify_settings_service = WorkNotifySettingsService(work_notify_store=work_notify_settings_store)
yonyou_work_notify_service = YonyouWorkNotifyService(work_notify_settings_service=yonyou_work_notify_settings_service)
yonyou_contacts_search_service = YonyouContactsSearchService(
    work_notify_settings_service=yonyou_work_notify_settings_service
)
skill_registry = build_skill_registry(knowledge_store, yonyou_work_notify_service)
chat_service = ChatService(
    thread_store,
    knowledge_store,
    skill_registry,
    llm_service,
    agent_store,
    provider_store,
    gitlab_import_service,
)
watcher_service = WatcherService(watcher_store, llm_service, settings, mail_service)
watcher_scheduler = WatcherScheduler(watcher_service, settings.watcher_scheduler_interval_seconds)
support_issue_service = SupportIssueService(
    support_issue_store=support_issue_store,
    knowledge_store=knowledge_store,
    llm_service=llm_service,
    feishu_service=feishu_service,
    mail_service=mail_service,
    yonyou_work_notify_service=yonyou_work_notify_service,
    yonyou_contacts_search_service=yonyou_contacts_search_service,
)
support_issue_scheduler = SupportIssueScheduler(
    support_issue_service,
    settings.support_issue_scheduler_interval_seconds,
)

# 这一段是后端“对象装配区”：
# 先把 Store / Service / Scheduler 串好，再把它们暴露成 HTTP 路由。
# 这样每个路由函数都能保持很薄，只负责接收请求并转发到业务层。
app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup() -> None:
    await watcher_scheduler.start()
    await support_issue_scheduler.start()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await watcher_scheduler.stop()
    await support_issue_scheduler.stop()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# -----------------------------
# Chat / 全局设置 / 知识库接口
# -----------------------------
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


@app.get("/api/settings/providers", response_model=list[ProviderConfig])
def list_providers() -> list[ProviderConfig]:
    return chat_service.list_providers()


@app.get("/api/settings/providers/{provider_id}", response_model=ProviderConfig)
def get_provider(provider_id: str) -> ProviderConfig:
    return chat_service.get_provider(provider_id)


@app.patch("/api/settings/providers/{provider_id}", response_model=ProviderConfig)
def update_provider(provider_id: str, request: UpdateProviderRequest) -> ProviderConfig:
    return chat_service.update_provider(provider_id, request)


@app.post("/api/settings/providers/{provider_id}/test", response_model=ProviderTestResponse)
def test_provider(provider_id: str, request: UpdateProviderRequest) -> ProviderTestResponse:
    return chat_service.test_provider(provider_id, request)


@app.get("/api/settings/mail", response_model=MailSettings)
def get_mail_settings() -> MailSettings:
    return mail_service.get_mail_settings()


@app.patch("/api/settings/mail", response_model=MailSettings)
def update_mail_settings(request: UpdateMailSettingsRequest) -> MailSettings:
    return mail_service.update_mail_settings(request)


@app.post("/api/settings/mail/test", response_model=MailTestResponse)
def test_mail_settings(request: MailTestRequest) -> MailTestResponse:
    return mail_service.test_mail_settings(request)


@app.get("/api/settings/feishu", response_model=FeishuSettings)
def get_feishu_settings() -> FeishuSettings:
    return feishu_service.get_feishu_settings()


@app.patch("/api/settings/feishu", response_model=FeishuSettings)
def update_feishu_settings(request: UpdateFeishuSettingsRequest) -> FeishuSettings:
    return feishu_service.update_feishu_settings(request)


@app.get("/api/settings/work-notify", response_model=WorkNotifySettings)
def get_work_notify_settings() -> WorkNotifySettings:
    return yonyou_work_notify_settings_service.get_work_notify_settings()


@app.patch("/api/settings/work-notify", response_model=WorkNotifySettings)
def update_work_notify_settings(request: UpdateWorkNotifySettingsRequest) -> WorkNotifySettings:
    return yonyou_work_notify_settings_service.update_work_notify_settings(request)


@app.get("/api/settings/rag-embedding", response_model=RAGEmbeddingSettings)
def get_rag_embedding_settings() -> RAGEmbeddingSettings:
    selection = knowledge_store.embedding_service.describe_runtime_selection()
    return RAGEmbeddingSettings(
        configured=selection.source != "fallback",
        config_source=selection.source,
        runtime_mode="provider"
        if selection.preferred_backend != knowledge_store.embedding_service.hashing_model_name
        else "hashing",
        provider_id=selection.provider_id,
        model=selection.model_name,
        timeout_seconds=selection.timeout_seconds,
        preferred_backend=selection.preferred_backend,
        indexed_backend=knowledge_store.indexed_embedding_backend,
        reindex_required=knowledge_store.indexed_embedding_backend not in {"", selection.preferred_backend},
    )


@app.patch("/api/settings/rag-embedding", response_model=RAGEmbeddingSettings)
def update_rag_embedding_settings(request: UpdateRAGEmbeddingSettingsRequest) -> RAGEmbeddingSettings:
    rag_embedding_settings_service.update_rag_embedding_settings(request)
    knowledge_store.rebuild_vector_index()
    return get_rag_embedding_settings()


@app.get("/api/settings/gitlab-import", response_model=GitLabImportSettings)
def get_gitlab_import_settings() -> GitLabImportSettings:
    return gitlab_settings_service.get_public_settings()


@app.patch("/api/settings/gitlab-import", response_model=GitLabImportSettings)
def update_gitlab_import_settings(request: UpdateGitLabImportSettingsRequest) -> GitLabImportSettings:
    return gitlab_settings_service.update_settings(request)


@app.get("/api/knowledge/tree", response_model=KnowledgeTreeResponse)
def get_knowledge_tree() -> KnowledgeTreeResponse:
    return chat_service.get_knowledge_tree()


@app.post("/api/knowledge/tree/nodes", response_model=KnowledgeTreeNode)
def create_knowledge_node(request: KnowledgeNodeCreateRequest) -> KnowledgeTreeNode:
    return chat_service.create_knowledge_node(request)


@app.post("/api/knowledge/tree/upload-directory", response_model=list[KnowledgeDocument])
def upload_directory(request: DirectoryUploadRequest) -> list[KnowledgeDocument]:
    return chat_service.upload_directory(request)


@app.post("/api/knowledge/tree/import-gitlab", response_model=GitLabTreeImportResponse)
def import_gitlab_tree(request: GitLabTreeImportRequest) -> GitLabTreeImportResponse:
    return chat_service.import_gitlab_tree(request)


@app.post("/api/knowledge/tree/{node_id}/documents", response_model=KnowledgeDocument)
def upload_document_to_node(node_id: str, request: UploadDocumentRequest) -> KnowledgeDocument:
    content = base64.b64decode(request.content_base64.encode("utf-8"))
    return chat_service.ingest_document(
        request.file_name or "untitled.txt",
        content,
        node_id=node_id,
        relative_path=request.relative_path,
    )


@app.delete("/api/knowledge/tree/{node_id}", response_model=KnowledgeDeleteResponse)
def delete_knowledge_node(node_id: str) -> KnowledgeDeleteResponse:
    return chat_service.delete_knowledge_node(node_id)


@app.get("/api/knowledge/tree/{node_id}", response_model=KnowledgeTreeNodeDetail)
def get_knowledge_node_detail(node_id: str) -> KnowledgeTreeNodeDetail:
    return chat_service.get_knowledge_node_detail(node_id)


@app.post("/api/knowledge/documents", response_model=KnowledgeDocument)
def upload_document(request: UploadDocumentRequest) -> KnowledgeDocument:
    content = base64.b64decode(request.content_base64.encode("utf-8"))
    return chat_service.ingest_document(
        request.file_name or "untitled.txt",
        content,
        relative_path=request.relative_path,
    )


@app.get("/api/knowledge/documents", response_model=list[KnowledgeDocument])
def list_documents() -> list[KnowledgeDocument]:
    return chat_service.list_documents()


@app.delete("/api/knowledge/documents/{document_id}", response_model=KnowledgeDeleteResponse)
def delete_knowledge_document(document_id: str) -> KnowledgeDeleteResponse:
    return chat_service.delete_knowledge_document(document_id)


@app.patch("/api/knowledge/documents/{document_id}", response_model=KnowledgeDocument)
def update_knowledge_document(document_id: str, request: UpdateKnowledgeDocumentRequest) -> KnowledgeDocument:
    return chat_service.update_knowledge_document(document_id, request)


@app.get("/api/knowledge/search", response_model=RetrievalResult)
def search_knowledge(query: str) -> RetrievalResult:
    return chat_service.search_knowledge(query)


@app.post("/api/retrieval/query", response_model=RetrievalResult)
def retrieval_query(request: RetrievalQueryRequest) -> RetrievalResult:
    return chat_service.query_retrieval(request)


# -----------------------------
# 配置型 Agent 接口
# -----------------------------
@app.get("/api/agents", response_model=list[AgentConfig])
def list_agents() -> list[AgentConfig]:
    return chat_service.list_agents()


@app.post("/api/agents", response_model=AgentConfig)
def create_agent(request: CreateAgentRequest) -> AgentConfig:
    return chat_service.create_agent(request)


@app.get("/api/agents/{agent_id}", response_model=AgentConfig)
def get_agent(agent_id: str) -> AgentConfig:
    return chat_service.get_agent(agent_id)


@app.patch("/api/agents/{agent_id}", response_model=AgentConfig)
def update_agent(agent_id: str, request: UpdateAgentRequest) -> AgentConfig:
    return chat_service.update_agent(agent_id, request)


@app.post("/api/agents/{agent_id}/run", response_model=AgentRunResponse)
def run_agent(agent_id: str, request: RunAgentRequest) -> AgentRunResponse:
    return chat_service.run_agent(agent_id, request)


# -----------------------------
# 巡检 Agent 接口
# -----------------------------
@app.get("/api/watchers", response_model=list[WatcherAgentConfig])
def list_watchers() -> list[WatcherAgentConfig]:
    return watcher_service.list_watchers()


@app.post("/api/watchers", response_model=WatcherAgentConfig)
def create_watcher(request: CreateWatcherRequest) -> WatcherAgentConfig:
    return watcher_service.create_watcher(request)


@app.get("/api/watchers/{watcher_id}", response_model=WatcherAgentConfig)
def get_watcher(watcher_id: str) -> WatcherAgentConfig:
    return watcher_service.get_watcher(watcher_id)


@app.patch("/api/watchers/{watcher_id}", response_model=WatcherAgentConfig)
def update_watcher(watcher_id: str, request: UpdateWatcherRequest) -> WatcherAgentConfig:
    return watcher_service.update_watcher(watcher_id, request)


@app.post("/api/watchers/test-fetch", response_model=WatcherFetchTestResponse)
def test_watcher_fetch(request: WatcherFetchTestRequest) -> WatcherFetchTestResponse:
    return watcher_service.test_fetch(request)


@app.post("/api/watchers/{watcher_id}/run", response_model=WatcherRun)
def run_watcher(watcher_id: str, request: RunWatcherRequest | None = None) -> WatcherRun:
    normalized_request = request or RunWatcherRequest()
    return watcher_service.run_watcher(
        watcher_id,
        force_email_snapshot=True,
        force_assign_snapshot=normalized_request.assign_current_list,
    )


@app.get("/api/watchers/{watcher_id}/runs", response_model=list[WatcherRun])
def list_watcher_runs(watcher_id: str) -> list[WatcherRun]:
    return watcher_service.list_runs(watcher_id)


# -----------------------------
# 支持问题 Agent 接口
# -----------------------------
@app.get("/api/support-agents", response_model=list[SupportIssueAgentConfig])
def list_support_agents() -> list[SupportIssueAgentConfig]:
    return support_issue_service.list_agents()


@app.post("/api/support-agents/validate-bitable", response_model=FeishuBitableValidationResponse)
def validate_support_agent_bitable(request: FeishuBitableValidationRequest) -> FeishuBitableValidationResponse:
    return support_issue_service.validate_bitable(request)


@app.post("/api/support-agents/preview-bitable", response_model=FeishuBitablePreviewResponse)
def preview_support_agent_bitable(request: FeishuBitablePreviewRequest) -> FeishuBitablePreviewResponse:
    return support_issue_service.preview_bitable(request)


@app.post("/api/support-agents/fields-bitable", response_model=FeishuBitableFieldsResponse)
def fields_support_agent_bitable(request: FeishuBitableFieldsRequest) -> FeishuBitableFieldsResponse:
    return support_issue_service.list_bitable_fields(request)


@app.post("/api/support-agents/pending-analysis-bitable", response_model=FeishuBitablePendingAnalysisResponse)
def pending_analysis_support_agent_bitable(
    request: FeishuBitablePendingAnalysisRequest,
) -> FeishuBitablePendingAnalysisResponse:
    return support_issue_service.list_pending_analysis_rows(request)


@app.post("/api/support-agents/validate-bitable-write", response_model=FeishuBitableWriteValidationResponse)
def validate_support_agent_bitable_write(
    request: FeishuBitableWriteValidationRequest,
) -> FeishuBitableWriteValidationResponse:
    return support_issue_service.validate_bitable_write(request)


@app.post("/api/support-agents", response_model=SupportIssueAgentConfig)
def create_support_agent(request: CreateSupportIssueAgentRequest) -> SupportIssueAgentConfig:
    return support_issue_service.create_agent(request)


@app.get("/api/support-agents/{agent_id}", response_model=SupportIssueAgentConfig)
def get_support_agent(agent_id: str) -> SupportIssueAgentConfig:
    return support_issue_service.get_agent(agent_id)


@app.patch("/api/support-agents/{agent_id}", response_model=SupportIssueAgentConfig)
def update_support_agent(agent_id: str, request: UpdateSupportIssueAgentRequest) -> SupportIssueAgentConfig:
    return support_issue_service.update_agent(agent_id, request)


@app.post("/api/support-agents/{agent_id}/run", response_model=SupportIssueRun)
def run_support_agent(agent_id: str, request: RunSupportIssueAgentRequest | None = None) -> SupportIssueRun:
    _ = request
    return support_issue_service.run_agent(agent_id)


@app.get("/api/support-agents/{agent_id}/runs", response_model=list[SupportIssueRun])
def list_support_agent_runs(agent_id: str) -> list[SupportIssueRun]:
    return support_issue_service.list_runs(agent_id)


@app.get("/api/support-agents/{agent_id}/insights", response_model=SupportIssueInsights)
def get_support_agent_insights(agent_id: str) -> SupportIssueInsights:
    return support_issue_service.get_insights(agent_id)


@app.post("/api/support-agents/{agent_id}/sync-feedback", response_model=SupportIssueFeedbackSyncResponse)
def sync_support_agent_feedback(agent_id: str) -> SupportIssueFeedbackSyncResponse:
    return support_issue_service.sync_feedback(agent_id)


@app.get("/api/support-agents/{agent_id}/case-candidates", response_model=list[SupportIssueCaseCandidate])
def list_support_agent_case_candidates(
    agent_id: str,
    status: str | None = None,
    category: str | None = None,
    keyword: str | None = None,
) -> list[SupportIssueCaseCandidate]:
    return support_issue_service.list_case_candidates(
        agent_id,
        status=status,
        category=category,
        keyword=keyword,
    )


@app.patch("/api/support-case-candidates/{candidate_id}", response_model=SupportIssueCaseCandidate)
def update_support_case_candidate(
    candidate_id: str,
    request: UpdateSupportIssueCaseCandidateRequest,
) -> SupportIssueCaseCandidate:
    return support_issue_service.review_case_candidate(candidate_id, request)


@app.post("/api/support-agents/{agent_id}/digest", response_model=SupportIssueDigestRun)
def run_support_agent_digest(agent_id: str) -> SupportIssueDigestRun:
    return support_issue_service.run_digest(agent_id, trigger_source="manual")


@app.get("/api/support-agents/{agent_id}/digest-runs", response_model=list[SupportIssueDigestRun])
def list_support_agent_digest_runs(agent_id: str) -> list[SupportIssueDigestRun]:
    return support_issue_service.list_digest_runs(agent_id)

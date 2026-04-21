"""对外接口与内部状态的结构化模型。

这一版 schema 比之前更完整，因为系统已经从单一 chat demo 升级为：
- Chat 工作台
- 检索模式（带知识树）
- 我的 Agent（配置型 Agent）

把模型集中在这里的好处是：无论你是看 API、看前端类型，还是想理解
LangChain / LangGraph / RAG 的数据流，都可以先从这些结构入手。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ScopeType = Literal["none", "global", "tree_recursive"]
ModelMode = Literal["learning", "provider"]
ProviderProtocol = Literal["openai_compatible", "anthropic_compatible", "ollama_native", "mock_local"]
ProviderModelSource = Literal["manual", "discovered"]

# -----------------------------
# 模型 / Provider / 邮箱 / 飞书
# -----------------------------


class ModelConfig(BaseModel):
    """统一的模型配置。"""

    mode: ModelMode = Field(default="learning", description="运行模式：learning 或 provider")
    provider: str = Field(default="mock", description="模型提供商，如 openai / ollama / mock")
    model: str = Field(default="learning-mode", description="模型名称")
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=1024, ge=128, le=8192)


class ProviderModel(BaseModel):
    """某个 provider 下可选的模型项。"""

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    source: ProviderModelSource = "manual"


class ProviderConfig(BaseModel):
    """返回给前端的 provider 配置。

    注意这里不返回 API Key 明文，而是只暴露：
    - has_api_key：是否已经配置；
    - api_key_masked：用于在界面里提示“当前已保存一个 key”。
    """

    id: str
    name: str
    enabled: bool = True
    protocol: ProviderProtocol
    allowed_protocols: list[ProviderProtocol] = Field(default_factory=list)
    api_base_url: str = ""
    has_api_key: bool = False
    api_key_masked: str | None = None
    models: list[ProviderModel] = Field(default_factory=list)
    locked: bool = True
    created_at: datetime
    updated_at: datetime


class ProviderRuntimeConfig(BaseModel):
    """后端运行时使用的 provider 配置。

    这一层保留 API Key 明文，只在服务内部流转，不直接对外暴露。
    """

    id: str
    name: str
    enabled: bool = True
    protocol: ProviderProtocol
    allowed_protocols: list[ProviderProtocol] = Field(default_factory=list)
    api_base_url: str = ""
    api_key: str | None = None
    models: list[ProviderModel] = Field(default_factory=list)
    locked: bool = True
    created_at: datetime
    updated_at: datetime


class UpdateProviderRequest(BaseModel):
    """更新 provider 配置。

    `api_key` 省略时表示保留原值；第一版不提供显式清空能力。
    """

    enabled: bool | None = None
    protocol: ProviderProtocol | None = None
    api_base_url: str | None = None
    api_key: str | None = None
    models: list[ProviderModel] | None = None


class ProviderTestResponse(BaseModel):
    """测试连接结果。"""

    ok: bool
    message: str
    available_models: list[ProviderModel] = Field(default_factory=list)


class MailSettings(BaseModel):
    """返回给前端的全局邮箱设置。"""

    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    has_password: bool = False
    password_masked: str | None = None
    use_tls: bool = True
    use_ssl: bool = False
    sender_email: str = ""


class MailRuntimeSettings(BaseModel):
    """服务内部使用的真实邮箱运行时配置。"""

    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str | None = None
    use_tls: bool = True
    use_ssl: bool = False
    sender_email: str = ""
    created_at: datetime
    updated_at: datetime


class UpdateMailSettingsRequest(BaseModel):
    """更新邮箱设置。"""

    enabled: bool | None = None
    smtp_host: str | None = None
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: str | None = None
    use_tls: bool | None = None
    use_ssl: bool | None = None


class WorkNotifySettings(BaseModel):
    """返回给前端的工作通知设置。"""

    configured: bool = False
    app_key: str = ""
    has_app_secret: bool = False
    app_secret_masked: str | None = None
    has_contacts_cookie: bool = False
    contacts_cookie_masked: str | None = None


class WorkNotifyRuntimeSettings(BaseModel):
    """服务内部使用的真实工作通知配置。"""

    app_key: str | None = None
    app_secret: str | None = None
    contacts_cookie: str | None = None
    created_at: datetime
    updated_at: datetime


class UpdateWorkNotifySettingsRequest(BaseModel):
    """更新工作通知设置。"""

    app_key: str | None = None
    app_secret: str | None = None
    contacts_cookie: str | None = None


class RAGEmbeddingSettings(BaseModel):
    """返回给前端的 RAG embedding 设置。

    这里同时区分三层概念：
    - `config_source`：配置来自环境变量、数据库，还是根本没配；
    - `runtime_mode`：当前按配置解析后，最终会走 provider 还是 hashing fallback；
    - `indexed_backend`：知识库里现有向量索引实际是按哪个 backend 建出来的。
    """

    configured: bool = False
    config_source: Literal["environment", "database", "fallback"] = "fallback"
    runtime_mode: Literal["provider", "hashing"] = "hashing"
    provider_id: str = ""
    model: str = ""
    timeout_seconds: int = 20
    preferred_backend: str = ""
    indexed_backend: str = ""
    reindex_required: bool = False


class RAGEmbeddingRuntimeSettings(BaseModel):
    """服务内部使用的真实 RAG embedding 配置。"""

    provider_id: str | None = None
    model: str | None = None
    timeout_seconds: int = Field(default=20, ge=5, le=120)
    created_at: datetime
    updated_at: datetime


class UpdateRAGEmbeddingSettingsRequest(BaseModel):
    """更新 RAG embedding 设置。"""

    provider_id: str | None = None
    model: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=5, le=120)


class MailTestRequest(BaseModel):
    """测试发信请求。"""

    recipient_email: str = Field(min_length=3)
    subject: str | None = None
    body: str | None = None


class MailTestResponse(BaseModel):
    """测试发信结果。"""

    ok: bool
    message: str
    sender_email: str = ""
    recipient_email: str = ""


class FeishuSettings(BaseModel):
    """返回给前端的飞书设置。"""

    configured: bool = False
    app_id: str = ""
    has_app_secret: bool = False
    app_secret_masked: str | None = None
    auth_mode: Literal["tenant_access_token_internal"] = "tenant_access_token_internal"


class FeishuRuntimeSettings(BaseModel):
    """服务内部使用的真实飞书运行时配置。"""

    app_id: str | None = None
    app_secret: str | None = None
    created_at: datetime
    updated_at: datetime


class UpdateFeishuSettingsRequest(BaseModel):
    """更新飞书设置。"""

    app_id: str | None = None
    app_secret: str | None = None


class GitLabImportSettings(BaseModel):
    """返回给前端的 GitLab 导入设置。"""

    configured: bool = False
    has_token: bool = False
    token_masked: str | None = None
    token_source: Literal["database", "environment", "none"] = "none"
    allowed_hosts: list[str] = Field(default_factory=list)


class GitLabImportStoredSettings(BaseModel):
    """数据库中保存的 GitLab 导入设置。"""

    token: str | None = None
    allowed_hosts: list[str] | None = None
    created_at: datetime
    updated_at: datetime


class GitLabImportRuntimeSettings(BaseModel):
    """服务内部使用的 GitLab 导入运行时配置。"""

    token: str | None = None
    allowed_hosts: list[str] = Field(default_factory=list)
    token_source: Literal["database", "environment", "none"] = "none"
    created_at: datetime
    updated_at: datetime


class UpdateGitLabImportSettingsRequest(BaseModel):
    """更新 GitLab 导入设置。"""

    token: str | None = None
    clear_token: bool = False
    allowed_hosts: list[str] | None = None


class FeishuBitableValidationRequest(BaseModel):
    """验证飞书多维表格地址。"""

    feishu_bitable_url: str = Field(min_length=1)


class FeishuBitableValidationResponse(BaseModel):
    """飞书多维表格地址验证结果。"""

    ok: bool
    message: str
    normalized_url: str = ""
    parsed_app_token: str = ""
    parsed_table_id: str = ""
    parsed_view_id: str | None = None


class FeishuBitablePreviewRequest(BaseModel):
    """预览飞书多维表格数据。"""

    feishu_bitable_url: str = Field(min_length=1)


class FeishuBitablePreviewResponse(BaseModel):
    """飞书多维表格前 5 行预览。"""

    ok: bool
    message: str
    normalized_url: str = ""
    parsed_app_token: str = ""
    parsed_table_id: str = ""
    parsed_view_id: str | None = None
    preview_rows: list[dict[str, Any]] = Field(default_factory=list)
    preview_count: int = 0
    has_more: bool = False


class FeishuBitableFieldsRequest(BaseModel):
    """读取飞书多维表格字段列表。"""

    feishu_bitable_url: str = Field(min_length=1)


class FeishuBitableFieldInfo(BaseModel):
    """飞书多维表格字段信息。"""

    field_id: str | None = None
    field_name: str
    type: int | None = None
    ui_type: str | None = None
    is_primary: bool = False
    property: dict[str, Any] = Field(default_factory=dict)


class FeishuBitableFieldsResponse(BaseModel):
    """飞书多维表格字段读取结果。"""

    ok: bool
    message: str
    normalized_url: str = ""
    parsed_app_token: str = ""
    parsed_table_id: str = ""
    parsed_view_id: str | None = None
    fields: list[FeishuBitableFieldInfo] = Field(default_factory=list)
    source: Literal["metadata_api", "preview_fallback"] = "metadata_api"


class FeishuBitablePendingAnalysisRequest(BaseModel):
    """筛选飞书多维表格中“待分析”数据。"""

    feishu_bitable_url: str = Field(min_length=1)
    progress_field_name: str = "回复进度"


class FeishuBitablePendingAnalysisRow(BaseModel):
    """待分析筛选结果中的单行。"""

    record_id: str = ""
    content: str = ""
    fields: dict[str, Any] = Field(default_factory=dict)


class FeishuBitablePendingAnalysisResponse(BaseModel):
    """飞书多维表格“待分析”筛选结果。"""

    ok: bool
    message: str
    normalized_url: str = ""
    parsed_app_token: str = ""
    parsed_table_id: str = ""
    parsed_view_id: str | None = None
    filter_field_name: str = "回复进度"
    filter_value: str = "待分析"
    content_field_name: str | None = None
    total_count: int = 0
    matched_count: int = 0
    rows: list[FeishuBitablePendingAnalysisRow] = Field(default_factory=list)


class FeishuBitableWriteValidationRequest(BaseModel):
    """验证飞书多维表格写权限。"""

    feishu_bitable_url: str = Field(min_length=1)
    question_field_name: str = ""
    answer_field_name: str = ""
    status_field_name: str = ""


class FeishuBitableWriteValidationResponse(BaseModel):
    """飞书多维表格写权限验证结果。"""

    ok: bool
    message: str
    normalized_url: str = ""
    parsed_app_token: str = ""
    parsed_table_id: str = ""
    parsed_view_id: str | None = None
    created_record_id: str | None = None
    updated_record_id: str | None = None
    deleted_record_id: str | None = None
    used_create_field_name: str | None = None
    used_update_field_name: str | None = None
    created_fields_preview: dict[str, Any] = Field(default_factory=dict)
    updated_fields_preview: dict[str, Any] = Field(default_factory=dict)


# -----------------------------
# Chat / Skill / 知识库 / 检索
# -----------------------------

class Citation(BaseModel):
    """RAG 命中的引用片段。"""

    document_id: str
    document_name: str
    chunk_id: str
    snippet: str
    tree_id: str | None = None
    tree_path: str | None = None
    relative_path: str | None = None
    source_type: str | None = None
    heading_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class FinalResponse(BaseModel):
    """最终返回给前端和用户的结构化输出。"""

    answer: str
    citations: list[Citation] = Field(default_factory=list)
    used_tools: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class ChatMessage(BaseModel):
    """会话中的单条消息。"""

    id: str
    role: Literal["human", "assistant", "system"]
    content: str
    created_at: datetime


class ToolEvent(BaseModel):
    """工具调用轨迹。"""

    id: str
    tool_name: str
    status: Literal["started", "completed", "failed"]
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime
    ended_at: datetime | None = None
    note: str | None = None


class SkillDescriptor(BaseModel):
    """Skill 的教学型描述信息。"""

    id: str
    name: str
    description: str
    category: Literal["core", "tool", "knowledge", "integration"]
    tools: list[str]
    enabled_by_default: bool = True
    requires_rag: bool = False
    learning_focus: list[str] = Field(default_factory=list)


class KnowledgeDocument(BaseModel):
    """知识库文档元信息。"""

    id: str
    node_id: str
    name: str
    type: str
    relative_path: str = ""
    status: Literal["processing", "ready", "error"]
    chunk_count: int = 0
    created_at: datetime
    error_message: str | None = None
    external_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeTreeNode(BaseModel):
    """知识树节点。"""

    id: str
    name: str
    parent_id: str | None
    path: str
    children_count: int = 0
    document_count: int = 0
    children: list["KnowledgeTreeNode"] = Field(default_factory=list)


class KnowledgeTreeResponse(BaseModel):
    """完整知识树。"""

    root: KnowledgeTreeNode


class KnowledgeTreeNodeDetail(BaseModel):
    """单个节点详情。"""

    node: KnowledgeTreeNode
    children: list[KnowledgeTreeNode] = Field(default_factory=list)
    documents: list[KnowledgeDocument] = Field(default_factory=list)
    recursive_document_count: int = 0
    recursive_children_count: int = 0


class KnowledgeDeleteResponse(BaseModel):
    """知识树删除结果。"""

    ok: bool = True
    message: str
    deleted_node_count: int = 0
    deleted_document_count: int = 0
    deleted_chunk_count: int = 0


class KnowledgeNodeCreateRequest(BaseModel):
    """手动创建树节点。"""

    name: str = Field(min_length=1)
    parent_id: str | None = None


class DirectoryUploadItem(BaseModel):
    """目录上传中的单个文件。"""

    file_name: str
    relative_path: str
    content_base64: str


class DirectoryUploadRequest(BaseModel):
    """目录上传请求。"""

    parent_node_id: str | None = None
    files: list[DirectoryUploadItem]


class GitLabTreeImportRequest(BaseModel):
    """GitLab 文档树导入请求。"""

    tree_url: str = Field(min_length=1)
    parent_node_id: str | None = None


class KnowledgeImportIssue(BaseModel):
    """批量导入中的单个异常项。"""

    path: str
    reason: str


class GitLabTreeImportResponse(BaseModel):
    """GitLab 文档树批量导入结果。"""

    source_url: str
    created_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    skipped_paths: list[str] = Field(default_factory=list)
    failed_items: list[KnowledgeImportIssue] = Field(default_factory=list)
    documents: list[KnowledgeDocument] = Field(default_factory=list)


class UploadDocumentRequest(BaseModel):
    """兼容旧版：上传到根节点或指定节点。"""

    file_name: str
    content_base64: str
    relative_path: str | None = None


class UpdateKnowledgeDocumentRequest(BaseModel):
    """更新知识文档元数据。"""

    external_url: str | None = None


RetrievalProfile = Literal["default", "support_issue"]


class RAGQueryVariant(BaseModel):
    """Query bundle 里的单条检索变体。"""

    label: str
    query: str
    source: str = ""


class RAGQueryBundle(BaseModel):
    """一次检索使用的 query bundle。"""

    original_query: str
    normalized_query: str
    rewritten_query: str = ""
    keyword_queries: list[str] = Field(default_factory=list)
    sub_queries: list[str] = Field(default_factory=list)
    must_terms: list[str] = Field(default_factory=list)
    filters: dict[str, str] = Field(default_factory=dict)
    query_variants: list[RAGQueryVariant] = Field(default_factory=list)


class RetrievalCandidateDebug(BaseModel):
    """检索候选或 rerank 结果的调试结构。"""

    chunk_id: str
    document_id: str
    document_name: str
    snippet: str
    tree_id: str | None = None
    tree_path: str | None = None
    relative_path: str | None = None
    source_type: str | None = None
    heading_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_query: str = ""
    query_label: str = ""
    matched_query_labels: list[str] = Field(default_factory=list)
    lexical_score: float = 0.0
    vector_score: float = 0.0
    fused_score: float = 0.0
    relevance_score: float = 0.0
    useful_for_answer: bool = False
    reason: str = ""


class RetrievalDebugInfo(BaseModel):
    """检索链路的最小必要调试信息。"""

    retrieval_profile: RetrievalProfile = "default"
    query_bundle: RAGQueryBundle
    candidate_count: int = 0
    selected_count: int = 0
    selected_chunks: list[RetrievalCandidateDebug] = Field(default_factory=list)
    rerank_preview: list[RetrievalCandidateDebug] = Field(default_factory=list)


class RetrievalQueryRequest(BaseModel):
    """检索模式请求。"""

    model_config = ConfigDict(populate_by_name=True)

    query: str = Field(min_length=1)
    scope_type: ScopeType = "global"
    scope_id: str | None = None
    retrieval_profile: RetrievalProfile = "default"
    model_settings: ModelConfig | None = Field(default=None, alias="model_config")


class RetrievalResult(BaseModel):
    """检索模式输出。"""

    query: str
    scope_type: ScopeType
    scope_id: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    retrieval_context: str = ""
    summary: str = ""
    related_document_links: list["RelatedDocumentLink"] = Field(default_factory=list)
    debug: RetrievalDebugInfo | None = None


class RelatedDocumentLink(BaseModel):
    """检索命中的文档外链。"""

    document_id: str
    document_name: str
    external_url: str


# -----------------------------
# 会话线程与配置型 Agent
# -----------------------------

class ThreadSummary(BaseModel):
    """左侧会话列表用的摘要。"""

    thread_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    last_message_preview: str = ""


class ThreadState(BaseModel):
    """线程详情：会话消息 + 最后一次运行结果。"""

    model_config = ConfigDict(populate_by_name=True)

    thread_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    model_settings: ModelConfig = Field(alias="model_config")
    enabled_skills: list[str]
    messages: list[ChatMessage] = Field(default_factory=list)
    tool_events: list[ToolEvent] = Field(default_factory=list)
    final_output: FinalResponse | None = None


class CreateThreadRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str | None = None
    model_settings: ModelConfig | None = Field(default=None, alias="model_config")
    enabled_skills: list[str] | None = None


class CreateThreadResponse(BaseModel):
    thread_id: str
    title: str


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    content: str = Field(min_length=1)
    model_settings: ModelConfig | None = Field(default=None, alias="model_config")
    enabled_skills: list[str] | None = None


class AgentConfig(BaseModel):
    """配置型 Agent。"""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    description: str = ""
    system_prompt: str = ""
    model_settings: ModelConfig = Field(alias="model_config")
    enabled_skills: list[str] = Field(default_factory=list)
    knowledge_scope_type: ScopeType = "none"
    knowledge_scope_id: str | None = None
    created_at: datetime
    updated_at: datetime


class CreateAgentRequest(BaseModel):
    """创建 Agent。"""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1)
    description: str = ""
    system_prompt: str = ""
    model_settings: ModelConfig | None = Field(default=None, alias="model_config")
    enabled_skills: list[str] | None = None
    knowledge_scope_type: ScopeType = "none"
    knowledge_scope_id: str | None = None


class UpdateAgentRequest(BaseModel):
    """更新 Agent。"""

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    model_settings: ModelConfig | None = Field(default=None, alias="model_config")
    enabled_skills: list[str] | None = None
    knowledge_scope_type: ScopeType | None = None
    knowledge_scope_id: str | None = None


class RunAgentRequest(BaseModel):
    """运行 Agent。"""

    content: str = Field(min_length=1)


class AgentRunResponse(BaseModel):
    """Agent 运行结果。"""

    agent: AgentConfig
    result: FinalResponse
    citations: list[Citation] = Field(default_factory=list)
    retrieval_context: str = ""


WatcherRunStatus = Literal["success", "no_change", "baseline_seeded", "partial_success", "failed"]
WatcherMatchSource = Literal["rule", "llm", "unmatched"]
WatcherAssignmentStatus = Literal["pending", "success", "failed", "skipped", "unmatched"]
WatcherRequestMethod = Literal["GET", "POST"]
WatcherMatchMode = Literal["llm_fallback", "fixed_match"]

# -----------------------------
# 巡检 Agent 相关模型
# -----------------------------

class OwnerRule(BaseModel):
    """负责人规则。

    这里把“规则分配”单独建模，是为了让你能直观看到：
    - service / module / keyword 是确定性规则；
    - assignee_code 统一保存分配目标，既可以是 PM 经办人编码，也可以是 Jira 转派人；
    - LLM 兜底只在规则没命中时才会介入。
    """

    model_config = ConfigDict(extra="ignore")

    assignee_code: str = ""
    services: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    customer_issue_types: list[str] = Field(default_factory=list)
    owner_name: str | None = None
    owner_email: str | None = None
    assignment_payload_template: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        for key in ("assignee_code", "owner_name", "owner_email"):
            current = data.get(key)
            if isinstance(current, str):
                data[key] = current.strip()
        return data


class ParsedBug(BaseModel):
    """模型/规则抽取后的标准化 Bug 结构。"""

    bug_id: str = Field(min_length=1)
    bug_aid: str = ""
    jira_issue_id: str = ""
    jira_form_token: str = ""
    jira_atl_token: str = ""
    title: str = ""
    service: str = ""
    module: str = ""
    category: str = ""
    customer_issue_type: str = ""
    status: str = ""
    assignee: str = ""
    reporter: str = ""
    priority: str = ""
    created_at: str = ""
    raw_excerpt: str = ""


class WatcherOwnerSuggestion(BaseModel):
    """负责人匹配结果。

    这一步先不关心是否真的调用分配接口成功，只表达：
    - 是否匹配到了负责人；
    - 匹配来自 rule 还是 llm；
    - 给前端和学习文档展示“为什么分给这个人”。
    """

    matched: bool = False
    assignee_code: str | None = None
    match_source: WatcherMatchSource = "unmatched"
    reason: str = ""


class WatcherAssignmentResult(BaseModel):
    """单个 Bug 在“匹配负责人 + 调分配接口”后的最终结果。"""

    bug_id: str
    bug_aid: str = ""
    jira_issue_id: str = ""
    jira_form_token: str = ""
    jira_atl_token: str = ""
    title: str = ""
    service: str = ""
    module: str = ""
    status: str = ""
    raw_excerpt: str = ""
    assignee_code: str | None = None
    owner_name: str | None = None
    owner_email: str | None = None
    match_source: WatcherMatchSource = "unmatched"
    match_reason: str = ""
    assignment_status: WatcherAssignmentStatus = "pending"
    assignment_message: str | None = None


class SeenBugRecord(BaseModel):
    """已见 Bug 记录。

    它是“只看新增 bug_id”的核心状态。只要某个 bug_id 已经出现过，
    后续轮巡就不会再次触发通知链路。
    """

    agent_id: str
    bug_id: str
    first_seen_at: datetime
    latest_title: str = ""
    latest_service: str = ""
    latest_module: str = ""
    latest_status: str = ""


class WatcherAgentConfig(BaseModel):
    """巡检 Agent 配置。

    和“我的 Agent”不同，这类 Agent 面向定时自动化：
    - 它有 dashboard URL、请求头、轮巡间隔；
    - 它面向新增 Bug 巡检，而不是用户手动输入对话。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    description: str = ""
    dashboard_url: str = Field(min_length=1)
    request_method: WatcherRequestMethod = "GET"
    request_headers: dict[str, str] = Field(default_factory=dict)
    request_body_json: dict[str, Any] | None = None
    request_body_text: str | None = None
    detail_url_template: str | None = None
    detail_request_method: WatcherRequestMethod = "GET"
    detail_request_headers: dict[str, str] = Field(default_factory=dict)
    detail_request_body_text: str | None = None
    match_mode: WatcherMatchMode = "llm_fallback"
    poll_interval_minutes: int = Field(default=30, ge=1, le=24 * 60)
    sender_email: str = ""
    recipient_emails: list[str] = Field(default_factory=list)
    model_settings: ModelConfig = Field(alias="model_config")
    enabled: bool = True
    owner_rules: list[OwnerRule] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    last_run_status: WatcherRunStatus | None = None
    last_new_bug_count: int = 0
    last_emailed: bool | None = None
    consecutive_failure_count: int = 0
    auto_disabled_at: datetime | None = None
    auto_disabled_reason: str | None = None


class CreateWatcherRequest(BaseModel):
    """创建巡检 Agent。"""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1)
    description: str = ""
    dashboard_url: str = Field(min_length=1)
    request_method: WatcherRequestMethod = "GET"
    request_headers: dict[str, str] = Field(default_factory=dict)
    request_body_json: dict[str, Any] | None = None
    request_body_text: str | None = None
    detail_url_template: str | None = None
    detail_request_method: WatcherRequestMethod = "GET"
    detail_request_headers: dict[str, str] = Field(default_factory=dict)
    detail_request_body_text: str | None = None
    match_mode: WatcherMatchMode = "llm_fallback"
    poll_interval_minutes: int = Field(default=30, ge=1, le=24 * 60)
    sender_email: str = ""
    recipient_emails: list[str] = Field(default_factory=list)
    model_settings: ModelConfig | None = Field(default=None, alias="model_config")
    enabled: bool = True
    owner_rules: list[OwnerRule] = Field(default_factory=list)


class UpdateWatcherRequest(BaseModel):
    """更新巡检 Agent。"""

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    description: str | None = None
    dashboard_url: str | None = None
    request_method: WatcherRequestMethod | None = None
    request_headers: dict[str, str] | None = None
    request_body_json: dict[str, Any] | None = None
    request_body_text: str | None = None
    detail_url_template: str | None = None
    detail_request_method: WatcherRequestMethod | None = None
    detail_request_headers: dict[str, str] | None = None
    detail_request_body_text: str | None = None
    match_mode: WatcherMatchMode | None = None
    poll_interval_minutes: int | None = Field(default=None, ge=1, le=24 * 60)
    sender_email: str | None = None
    recipient_emails: list[str] | None = None
    model_settings: ModelConfig | None = Field(default=None, alias="model_config")
    enabled: bool | None = None
    owner_rules: list[OwnerRule] | None = None


class WatcherFetchTestRequest(BaseModel):
    """用于“接口检查”的临时请求配置。"""

    dashboard_url: str = Field(min_length=1)
    request_method: WatcherRequestMethod = "GET"
    request_headers: dict[str, str] = Field(default_factory=dict)
    request_body_json: dict[str, Any] | None = None
    request_body_text: str | None = None
    detail_url_template: str | None = None
    detail_request_method: WatcherRequestMethod = "GET"
    detail_request_headers: dict[str, str] = Field(default_factory=dict)
    detail_request_body_text: str | None = None


class RunWatcherRequest(BaseModel):
    """手动立即运行巡检 Agent。"""

    assign_current_list: bool = False


class WatcherFetchTestResponse(BaseModel):
    """接口检查结果。"""

    ok: bool
    status_code: int
    message: str
    dashboard_url: str
    request_method: WatcherRequestMethod
    request_headers: dict[str, str] = Field(default_factory=dict)
    request_body_json: dict[str, Any] | None = None
    request_body_text: str | None = None
    detail_url_template: str | None = None
    detail_request_method: WatcherRequestMethod = "GET"
    detail_request_headers: dict[str, str] = Field(default_factory=dict)
    detail_request_body_text: str | None = None
    response_content_type: str = ""
    response_body_preview: str = ""
    parsed_item_count: int = 0
    parsed_bug_count: int = 0
    parsed_bug_preview: list[ParsedBug] = Field(default_factory=list)



class WatcherRun(BaseModel):
    """一次巡检运行的完整记录。"""

    id: str
    agent_id: str
    status: WatcherRunStatus
    started_at: datetime
    ended_at: datetime | None = None
    fetched_count: int = 0
    parsed_count: int = 0
    new_bug_count: int = 0
    assigned_count: int = 0
    emailed: bool = False
    summary: str = ""
    error_message: str | None = None
    assignment_results: list[WatcherAssignmentResult] = Field(default_factory=list)


SupportIssueRunStatus = Literal["success", "no_change", "partial_success", "failed"]
SupportIssueRowResultStatus = Literal["generated", "manual_review", "no_hit", "failed"]
SupportIssueCaseCandidateStatus = Literal["pending_review", "approved"]
SupportIssueCaseReviewAction = Literal["save_edit", "approve_and_publish"]
SupportIssueDigestRunStatus = Literal["success", "failed"]
SupportIssueDigestTriggerSource = Literal["manual", "scheduled"]
SupportIssueNotificationEventType = Literal["manual_review_assigned", "registrant_confirmed"]
SupportIssueNotificationEventStatus = Literal["sent", "skipped", "failed"]
SupportIssueGraphTracePhase = Literal["run", "row", "feedback", "digest"]
SupportIssueGraphTraceStatus = Literal["success", "skipped", "failed"]

# -----------------------------
# 支持问题 Agent 相关模型
# -----------------------------

class SupportIssueFeedbackSnapshot(BaseModel):
    """单行处理时刻抓取到的人工反馈快照。"""

    result: str = ""
    final_solution: str = ""
    comment: str = ""


class SupportIssueGraphTraceEvent(BaseModel):
    """支持问题 Agent 的轻量执行轨迹事件。

    这份结构专门服务于 LangGraph 学习场景：
    - `node` / `phase` 让你能看清楚当前节点属于哪条图；
    - `status` / `message` 让你知道节点是成功、跳过还是失败；
    - `payload_preview` 只保留调试摘要，避免把整份大状态直接塞到前端。
    """

    node: str
    phase: SupportIssueGraphTracePhase
    status: SupportIssueGraphTraceStatus
    started_at: datetime
    ended_at: datetime
    message: str = ""
    record_id: str | None = None
    payload_preview: dict[str, object] = Field(default_factory=dict)


class SupportIssueOwnerRule(BaseModel):
    """按业务模块匹配人工确认负责人。"""

    module_value: str = ""
    yht_user_id: str = ""


class SupportIssueClassificationResult(BaseModel):
    """分类子 agent 的结构化输出。"""

    category: str = ""
    composed_query: str = ""
    reasoning: str = ""
    supervisor_notes: str = ""


class SupportIssueEvidenceResult(BaseModel):
    """证据子 agent 的结构化输出。"""

    retrieval_hit_count: int = 0
    evidence_summary: str = ""
    no_hit: bool = False
    source_note: str = ""


class SupportIssueDraftResult(BaseModel):
    """草稿子 agent 的结构化输出。"""

    solution: str = ""
    reasoning: str = ""
    used_similar_case_count: int = 0


class SupportIssueReviewResult(BaseModel):
    """复核子 agent 的结构化输出。"""

    judge_status: Literal["pass", "manual_review"] = "manual_review"
    confidence_score: float = 0.0
    judge_reason: str = ""
    progress_value: str = ""
    reviewer_notes: str = ""


class SupportIssueRowResult(BaseModel):
    """支持问题 Agent 的单行处理结果。"""

    record_id: str
    source_record_id: str = ""
    source_table_id: str = ""
    source_table_name: str = ""
    source_bitable_url: str = ""
    question: str = ""
    status: SupportIssueRowResultStatus
    solution: str = ""
    related_link: str | None = None
    message: str = ""
    retrieval_hit_count: int = 0
    confidence_score: float = 0.0
    judge_status: str = ""
    judge_reason: str = ""
    question_category: str = ""
    similar_case_count: int = 0
    feedback_snapshot: SupportIssueFeedbackSnapshot | None = None
    classification_result: SupportIssueClassificationResult | None = None
    evidence_result: SupportIssueEvidenceResult | None = None
    draft_result: SupportIssueDraftResult | None = None
    review_result: SupportIssueReviewResult | None = None
    graph_trace: list[SupportIssueGraphTraceEvent] = Field(default_factory=list)


class SupportIssueAgentConfig(BaseModel):
    """支持问题 Agent 配置。"""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    description: str = ""
    enabled: bool = True
    poll_interval_minutes: int = Field(default=30, ge=1, le=24 * 60)
    feishu_bitable_url: str = Field(min_length=1)
    feishu_app_token: str = Field(min_length=1)
    feishu_table_id: str = Field(min_length=1)
    model_settings: ModelConfig = Field(alias="model_config")
    knowledge_scope_type: ScopeType = "global"
    knowledge_scope_id: str | None = None
    question_field_name: str = "问题"
    answer_field_name: str = "AI解决方案"
    link_field_name: str = "相关文档链接"
    progress_field_name: str = "回复进度"
    status_field_name: str = "处理状态"
    module_field_name: str = "负责模块"
    registrant_field_name: str = "登记人"
    feedback_result_field_name: str = "人工处理结果"
    feedback_final_answer_field_name: str = "人工最终方案"
    feedback_comment_field_name: str = "反馈备注"
    confidence_field_name: str = "AI置信度"
    hit_count_field_name: str = "命中知识数"
    support_owner_rules: list[SupportIssueOwnerRule] = Field(default_factory=list)
    fallback_support_yht_user_id: str = ""
    digest_enabled: bool = False
    digest_recipient_emails: list[str] = Field(default_factory=list)
    case_review_enabled: bool = True
    created_at: datetime
    updated_at: datetime
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    last_digest_at: datetime | None = None
    next_digest_at: datetime | None = None
    last_run_status: SupportIssueRunStatus | None = None
    last_run_summary: str | None = None


class CreateSupportIssueAgentRequest(BaseModel):
    """创建支持问题 Agent。"""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1)
    description: str = ""
    enabled: bool = True
    poll_interval_minutes: int = Field(default=30, ge=1, le=24 * 60)
    feishu_bitable_url: str = Field(min_length=1)
    model_settings: ModelConfig | None = Field(default=None, alias="model_config")
    knowledge_scope_type: ScopeType = "global"
    knowledge_scope_id: str | None = None
    question_field_name: str = "问题"
    answer_field_name: str = "AI解决方案"
    link_field_name: str = "相关文档链接"
    progress_field_name: str = "回复进度"
    status_field_name: str = "处理状态"
    module_field_name: str = "负责模块"
    registrant_field_name: str = "登记人"
    feedback_result_field_name: str = "人工处理结果"
    feedback_final_answer_field_name: str = "人工最终方案"
    feedback_comment_field_name: str = "反馈备注"
    confidence_field_name: str = "AI置信度"
    hit_count_field_name: str = "命中知识数"
    support_owner_rules: list[SupportIssueOwnerRule] = Field(default_factory=list)
    fallback_support_yht_user_id: str = ""
    digest_enabled: bool = False
    digest_recipient_emails: list[str] = Field(default_factory=list)
    case_review_enabled: bool = True


class UpdateSupportIssueAgentRequest(BaseModel):
    """更新支持问题 Agent。"""

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    poll_interval_minutes: int | None = Field(default=None, ge=1, le=24 * 60)
    feishu_bitable_url: str | None = None
    model_settings: ModelConfig | None = Field(default=None, alias="model_config")
    knowledge_scope_type: ScopeType | None = None
    knowledge_scope_id: str | None = None
    question_field_name: str | None = None
    answer_field_name: str | None = None
    link_field_name: str | None = None
    progress_field_name: str | None = None
    status_field_name: str | None = None
    module_field_name: str | None = None
    registrant_field_name: str | None = None
    feedback_result_field_name: str | None = None
    feedback_final_answer_field_name: str | None = None
    feedback_comment_field_name: str | None = None
    confidence_field_name: str | None = None
    hit_count_field_name: str | None = None
    support_owner_rules: list[SupportIssueOwnerRule] | None = None
    fallback_support_yht_user_id: str | None = None
    digest_enabled: bool | None = None
    digest_recipient_emails: list[str] | None = None
    case_review_enabled: bool | None = None


class RunSupportIssueAgentRequest(BaseModel):
    """手动立即运行支持问题 Agent。"""

    pass


class SupportIssueRun(BaseModel):
    """支持问题 Agent 的一次运行记录。"""

    id: str
    agent_id: str
    status: SupportIssueRunStatus
    started_at: datetime
    ended_at: datetime | None = None
    fetched_row_count: int = 0
    processed_row_count: int = 0
    generated_count: int = 0
    manual_review_count: int = 0
    no_hit_count: int = 0
    failed_count: int = 0
    summary: str = ""
    error_message: str | None = None
    row_results: list[SupportIssueRowResult] = Field(default_factory=list)
    graph_trace: list[SupportIssueGraphTraceEvent] = Field(default_factory=list)


class SupportIssueCategoryStat(BaseModel):
    """问题分类分布统计项。"""

    category: str
    count: int


class SupportIssueInsights(BaseModel):
    """支持问题 Agent 的效果分析与优化建议。"""

    agent_id: str
    sample_run_count: int = 0
    total_processed_count: int = 0
    generated_count: int = 0
    manual_review_count: int = 0
    no_hit_count: int = 0
    failed_count: int = 0
    acceptance_count: int = 0
    revised_acceptance_count: int = 0
    rejected_count: int = 0
    pending_confirm_count: int = 0
    acceptance_rate: float = 0.0
    rejection_rate: float = 0.0
    low_confidence_rate: float = 0.0
    no_hit_rate: float = 0.0
    manual_rewrite_rate: float = 0.0
    top_categories: list[SupportIssueCategoryStat] = Field(default_factory=list)
    optimization_suggestions: list[str] = Field(default_factory=list)


class SupportIssueFeedbackFact(BaseModel):
    """飞书反馈字段的结构化快照。

    这个模型承接“飞书表格是业务入口，但平台需要结构化沉淀”的设计：
    - 飞书里保存协作过程；
    - 平台库里保存当前最新事实；
    - 后续 digest、候选案例、趋势分析都基于这份事实表展开。
    """

    id: str
    agent_id: str
    record_id: str
    question: str = ""
    progress_value: str = ""
    ai_solution: str = ""
    related_links: list[str] = Field(default_factory=list)
    feedback_result: str = ""
    feedback_final_answer: str = ""
    feedback_comment: str = ""
    confidence_score: float = 0.0
    retrieval_hit_count: int = 0
    question_category: str = ""
    source_bitable_url: str = ""
    created_at: datetime
    updated_at: datetime
    last_synced_at: datetime


class SupportIssueFeedbackSyncResponse(BaseModel):
    """手动/自动同步飞书反馈后的摘要结果。"""

    agent_id: str
    synced_row_count: int = 0
    fact_upsert_count: int = 0
    history_appended_count: int = 0
    candidate_created_count: int = 0
    candidate_updated_count: int = 0
    summary: str = ""
    graph_trace: list[SupportIssueGraphTraceEvent] = Field(default_factory=list)


class SupportIssueCaseCandidate(BaseModel):
    """案例候选池中的候选项。

    候选项本质上是“已经有人类确认价值，但还没进入正式知识库”的中间层。
    这样可以避免把未经审核的人工答案直接写进正式案例库。
    """

    id: str
    agent_id: str
    record_id: str
    status: SupportIssueCaseCandidateStatus = "pending_review"
    question: str = ""
    ai_draft: str = ""
    feedback_result: str = ""
    final_solution: str = ""
    feedback_comment: str = ""
    confidence_score: float = 0.0
    retrieval_hit_count: int = 0
    question_category: str = ""
    related_links: list[str] = Field(default_factory=list)
    source_bitable_url: str = ""
    review_comment: str = ""
    knowledge_document_id: str | None = None
    approved_at: datetime | None = None
    approved_by: str | None = None
    created_at: datetime
    updated_at: datetime


class UpdateSupportIssueCaseCandidateRequest(BaseModel):
    """更新案例候选池中的单条候选。

    两态化改造后，候选页只保留两类显式动作：
    - `save_edit`：保存人工最终方案 / 反馈备注，通常仍停留在待审核；
    - `approve_and_publish`：审核通过并写入正式案例库。

    为了减少前后端接口数量，这个请求同时承载“内容编辑 + 审核动作”：
    - 当页面只是保存修改时，传 `save_edit`；
    - 当页面直接点击“通过并入库”时，可以把最新草稿一并提交。
    """

    action: SupportIssueCaseReviewAction
    reviewer_name: str = "平台管理员"
    review_comment: str = ""
    final_solution: str | None = None
    feedback_comment: str | None = None
    sync_to_feishu: bool = True


class SupportIssueDigestRun(BaseModel):
    """单个 Support Agent 的汇总运行记录。"""

    id: str
    agent_id: str
    status: SupportIssueDigestRunStatus
    trigger_source: SupportIssueDigestTriggerSource = "manual"
    started_at: datetime
    ended_at: datetime | None = None
    period_start: datetime
    period_end: datetime
    recipient_emails: list[str] = Field(default_factory=list)
    email_sent: bool = False
    email_subject: str = ""
    summary: str = ""
    error_message: str | None = None
    total_processed_count: int = 0
    generated_count: int = 0
    manual_review_count: int = 0
    no_hit_count: int = 0
    failed_count: int = 0
    acceptance_count: int = 0
    revised_acceptance_count: int = 0
    rejected_count: int = 0
    acceptance_rate: float = 0.0
    rejection_rate: float = 0.0
    low_confidence_rate: float = 0.0
    no_hit_rate: float = 0.0
    manual_rewrite_rate: float = 0.0
    top_categories: list[SupportIssueCategoryStat] = Field(default_factory=list)
    top_no_hit_topics: list[str] = Field(default_factory=list)
    highlight_samples: list[str] = Field(default_factory=list)
    knowledge_gap_suggestions: list[str] = Field(default_factory=list)
    new_candidate_count: int = 0
    approved_candidate_count: int = 0
    graph_trace: list[SupportIssueGraphTraceEvent] = Field(default_factory=list)


class SupportIssueNotificationEvent(BaseModel):
    """支持问题 Agent 的通知日志。"""

    id: str
    agent_id: str
    record_id: str
    event_type: SupportIssueNotificationEventType
    recipient_user_id: str = ""
    status: SupportIssueNotificationEventStatus
    error_message: str | None = None
    created_at: datetime


class CatalogResponse(BaseModel):
    """Catalog 信息。"""

    models: list[ModelConfig]
    skills: list[SkillDescriptor]
    tools: list[dict[str, Any]]
    learning_focus: list[dict[str, str]]

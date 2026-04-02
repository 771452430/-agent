/**
 * 前端共享类型定义。
 *
 * 这些类型尽量与后端 `backend/app/schemas.py` 保持同构，
 * 这样页面组件在读写接口数据时就能直接复用稳定的字段语义。
 */

/**
 * 模型与 Provider 相关类型。
 *
 * 这一组类型描述“系统可以连哪些模型厂商、当前运行想选哪个模型”，
 * 会被 Chat、检索模式、巡检 Agent、支持问题 Agent 共用。
 */
export type ScopeType = "none" | "global" | "tree_recursive";
export type ModelMode = "learning" | "provider";
export type ProviderProtocol = "openai_compatible" | "anthropic_compatible" | "ollama_native" | "mock_local";
export type ProviderModelSource = "manual" | "discovered";

export type ModelConfig = {
  mode: ModelMode;
  provider: string;
  model: string;
  temperature: number;
  max_tokens: number;
};

export type ProviderModel = {
  id: string;
  label: string;
  source: ProviderModelSource;
};

export type ProviderConfig = {
  id: string;
  name: string;
  enabled: boolean;
  protocol: ProviderProtocol;
  allowed_protocols: ProviderProtocol[];
  api_base_url: string;
  has_api_key: boolean;
  api_key_masked?: string | null;
  models: ProviderModel[];
  locked: boolean;
  created_at: string;
  updated_at: string;
};

export type UpdateProviderRequest = Partial<{
  enabled: boolean;
  protocol: ProviderProtocol;
  api_base_url: string;
  api_key: string;
  models: ProviderModel[];
}>;

export type ProviderTestResponse = {
  ok: boolean;
  message: string;
  available_models: ProviderModel[];
};

/** 邮箱设置类型：供全局 SMTP 面板和通知能力复用。 */
export type MailSettings = {
  enabled: boolean;
  smtp_host: string;
  smtp_port: number;
  smtp_username: string;
  has_password: boolean;
  password_masked?: string | null;
  use_tls: boolean;
  use_ssl: boolean;
  sender_email: string;
};

export type UpdateMailSettingsRequest = Partial<{
  enabled: boolean;
  smtp_host: string;
  smtp_port: number;
  smtp_username: string;
  smtp_password: string;
  use_tls: boolean;
  use_ssl: boolean;
}>;

export type MailTestRequest = {
  recipient_email: string;
  subject?: string;
  body?: string;
};

export type MailTestResponse = {
  ok: boolean;
  message: string;
  sender_email: string;
  recipient_email: string;
};

/** 飞书设置类型：主要服务于支持问题 Agent 的表格读取与回写。 */
export type FeishuSettings = {
  configured: boolean;
  app_id: string;
  has_app_secret: boolean;
  app_secret_masked?: string | null;
  auth_mode: "tenant_access_token_internal";
};

export type WorkNotifySettings = {
  configured: boolean;
  app_key: string;
  has_app_secret: boolean;
  app_secret_masked?: string | null;
};

export type UpdateWorkNotifySettingsRequest = Partial<{
  app_key: string;
  app_secret: string;
}>;

export type GitLabImportSettings = {
  configured: boolean;
  has_token: boolean;
  token_masked?: string | null;
  token_source: "database" | "environment" | "none";
  allowed_hosts: string[];
};

export type FeishuBitableValidationResponse = {
  ok: boolean;
  message: string;
  normalized_url: string;
  parsed_app_token: string;
  parsed_table_id: string;
  parsed_view_id?: string | null;
};

export type FeishuBitablePreviewRow = {
  record_id: string;
  fields: Record<string, unknown>;
};

export type FeishuBitablePreviewResponse = {
  ok: boolean;
  message: string;
  normalized_url: string;
  parsed_app_token: string;
  parsed_table_id: string;
  parsed_view_id?: string | null;
  preview_rows: FeishuBitablePreviewRow[];
  preview_count: number;
  has_more: boolean;
};

export type FeishuBitableFieldInfo = {
  field_id?: string | null;
  field_name: string;
  type?: number | null;
  ui_type?: string | null;
  is_primary: boolean;
  property: Record<string, unknown>;
};

export type FeishuBitableFieldsResponse = {
  ok: boolean;
  message: string;
  normalized_url: string;
  parsed_app_token: string;
  parsed_table_id: string;
  parsed_view_id?: string | null;
  fields: FeishuBitableFieldInfo[];
  source: "metadata_api" | "preview_fallback";
};

export type FeishuBitablePendingAnalysisRow = {
  record_id: string;
  content: string;
  fields: Record<string, unknown>;
};

export type FeishuBitablePendingAnalysisResponse = {
  ok: boolean;
  message: string;
  normalized_url: string;
  parsed_app_token: string;
  parsed_table_id: string;
  parsed_view_id?: string | null;
  filter_field_name: string;
  filter_value: string;
  content_field_name?: string | null;
  total_count: number;
  matched_count: number;
  rows: FeishuBitablePendingAnalysisRow[];
};

export type FeishuBitableWriteValidationResponse = {
  ok: boolean;
  message: string;
  normalized_url: string;
  parsed_app_token: string;
  parsed_table_id: string;
  parsed_view_id?: string | null;
  created_record_id?: string | null;
  updated_record_id?: string | null;
  deleted_record_id?: string | null;
  used_create_field_name?: string | null;
  used_update_field_name?: string | null;
  created_fields_preview: Record<string, unknown>;
  updated_fields_preview: Record<string, unknown>;
};

/**
 * Chat / 检索 / Skill 相关类型。
 *
 * 这一组类型决定了前端如何展示消息、工具轨迹、引用片段和最终答案。
 */
export type Citation = {
  document_id: string;
  document_name: string;
  chunk_id: string;
  snippet: string;
  tree_id?: string | null;
  tree_path?: string | null;
  relative_path?: string | null;
  source_type?: string | null;
};

export type ToolEvent = {
  id: string;
  tool_name: string;
  status: "started" | "completed" | "failed";
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  started_at: string;
  ended_at?: string | null;
  note?: string | null;
};

export type ChatMessage = {
  id?: string;
  role: "human" | "assistant" | "system";
  content: string;
  created_at?: string;
};

export type FinalResponse = {
  answer: string;
  citations: Citation[];
  used_tools: string[];
  next_actions: string[];
};

export type SkillDescriptor = {
  id: string;
  name: string;
  description: string;
  category: "core" | "tool" | "knowledge" | "integration";
  tools: string[];
  enabled_by_default: boolean;
  requires_rag: boolean;
  learning_focus: string[];
};

export type ThreadSummary = {
  thread_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  last_message_preview: string;
};

export type ThreadState = {
  thread_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  model_config: ModelConfig;
  enabled_skills: string[];
  messages: ChatMessage[];
  tool_events: ToolEvent[];
  final_output: FinalResponse | null;
};

export type Catalog = {
  models: ModelConfig[];
  skills: SkillDescriptor[];
  tools: {
    name: string;
    description: string;
    skill_id: string;
    category: string;
    learning_focus: string[];
  }[];
  learning_focus: { name: string; description: string }[];
};

/** 知识库与知识树相关类型。 */
export type KnowledgeDocument = {
  id: string;
  node_id: string;
  name: string;
  type: string;
  relative_path: string;
  status: "processing" | "ready" | "error";
  chunk_count: number;
  created_at: string;
  error_message?: string | null;
  external_url?: string | null;
};

export type KnowledgeTreeNode = {
  id: string;
  name: string;
  parent_id?: string | null;
  path: string;
  children_count: number;
  document_count: number;
  children: KnowledgeTreeNode[];
};

export type KnowledgeTreeResponse = {
  root: KnowledgeTreeNode;
};

export type KnowledgeTreeNodeDetail = {
  node: KnowledgeTreeNode;
  children: KnowledgeTreeNode[];
  documents: KnowledgeDocument[];
  recursive_document_count: number;
  recursive_children_count: number;
};

export type KnowledgeDeleteResponse = {
  ok: boolean;
  message: string;
  deleted_node_count: number;
  deleted_document_count: number;
  deleted_chunk_count: number;
};

export type KnowledgeImportIssue = {
  path: string;
  reason: string;
};

export type GitLabTreeImportResponse = {
  source_url: string;
  created_count: number;
  updated_count: number;
  skipped_count: number;
  failed_count: number;
  skipped_paths: string[];
  failed_items: KnowledgeImportIssue[];
  documents: KnowledgeDocument[];
};

export type RetrievalResult = {
  query: string;
  scope_type: ScopeType;
  scope_id?: string | null;
  citations: Citation[];
  retrieval_context: string;
  summary: string;
  related_document_links: Array<{
    document_id: string;
    document_name: string;
    external_url: string;
  }>;
};

export type AgentConfig = {
  id: string;
  name: string;
  description: string;
  system_prompt: string;
  model_config: ModelConfig;
  enabled_skills: string[];
  knowledge_scope_type: ScopeType;
  knowledge_scope_id?: string | null;
  created_at: string;
  updated_at: string;
};

export type AgentRunResponse = {
  agent: AgentConfig;
  result: FinalResponse;
  citations: Citation[];
  retrieval_context: string;
};

/**
 * 巡检 Agent 类型。
 *
 * 这些类型会同时出现在配置页面、抓取测试、责任人匹配和运行记录中。
 */
export type WatcherRunStatus = "success" | "no_change" | "baseline_seeded" | "partial_success" | "failed";
export type WatcherMatchSource = "rule" | "llm" | "unmatched";
export type WatcherAssignmentStatus = "pending" | "success" | "failed" | "skipped" | "unmatched";
export type WatcherRequestMethod = "GET" | "POST";

export type OwnerRule = {
  assignee_code: string;
  services: string[];
  modules: string[];
  keywords: string[];
  assignment_payload_template?: Record<string, unknown> | null;
  owner_name?: string | null;
  owner_email?: string | null;
};

export type ParsedBug = {
  bug_id: string;
  bug_aid: string;
  title: string;
  service: string;
  module: string;
  category: string;
  status: string;
  assignee: string;
  reporter: string;
  priority: string;
  created_at: string;
  raw_excerpt: string;
};

export type WatcherAssignmentResult = {
  bug_id: string;
  bug_aid: string;
  title: string;
  service: string;
  module: string;
  status: string;
  raw_excerpt: string;
  assignee_code?: string | null;
  owner_name?: string | null;
  owner_email?: string | null;
  match_source: WatcherMatchSource;
  match_reason: string;
  assignment_status: WatcherAssignmentStatus;
  assignment_message?: string | null;
};

export type WatcherAgentConfig = {
  id: string;
  name: string;
  description: string;
  dashboard_url: string;
  request_method: WatcherRequestMethod;
  request_headers: Record<string, string>;
  request_body_json?: Record<string, unknown> | null;
  poll_interval_minutes: number;
  sender_email: string;
  recipient_emails: string[];
  model_config: ModelConfig;
  enabled: boolean;
  owner_rules: OwnerRule[];
  created_at: string;
  updated_at: string;
  last_run_at?: string | null;
  next_run_at?: string | null;
  last_run_status?: WatcherRunStatus | null;
  last_new_bug_count: number;
  last_emailed?: boolean | null;
  consecutive_failure_count: number;
  auto_disabled_at?: string | null;
  auto_disabled_reason?: string | null;
};

export type WatcherRun = {
  id: string;
  agent_id: string;
  status: WatcherRunStatus;
  started_at: string;
  ended_at?: string | null;
  fetched_count: number;
  parsed_count: number;
  new_bug_count: number;
  assigned_count: number;
  emailed: boolean;
  summary: string;
  error_message?: string | null;
  assignment_results: WatcherAssignmentResult[];
};

export type WatcherFetchTestResponse = {
  ok: boolean;
  status_code: number;
  message: string;
  dashboard_url: string;
  request_method: WatcherRequestMethod;
  request_headers: Record<string, string>;
  request_body_json?: Record<string, unknown> | null;
  response_content_type: string;
  response_body_preview: string;
  parsed_item_count: number;
  parsed_bug_count: number;
  parsed_bug_preview: ParsedBug[];
};

/**
 * 支持问题 Agent 类型。
 *
 * 它们描述飞书问题行、运行结果、反馈事实、案例候选与 digest 汇总，
 * 是整个支持问题工作区的数据基础。
 */
export type SupportIssueRunStatus = "success" | "no_change" | "partial_success" | "failed";
export type SupportIssueRowResultStatus = "generated" | "manual_review" | "no_hit" | "failed";
export type SupportIssueCaseCandidateStatus = "pending_review" | "approved";
export type SupportIssueDigestRunStatus = "success" | "failed";

export type SupportIssueFeedbackSnapshot = {
  result: string;
  final_solution: string;
  comment: string;
};

export type SupportIssueRowResult = {
  record_id: string;
  question: string;
  status: SupportIssueRowResultStatus;
  solution: string;
  related_link?: string | null;
  message: string;
  retrieval_hit_count: number;
  confidence_score: number;
  judge_status: string;
  judge_reason: string;
  question_category: string;
  similar_case_count: number;
  feedback_snapshot?: SupportIssueFeedbackSnapshot | null;
};

export type SupportIssueAgentConfig = {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  poll_interval_minutes: number;
  feishu_bitable_url: string;
  feishu_app_token: string;
  feishu_table_id: string;
  model_config: ModelConfig;
  knowledge_scope_type: ScopeType;
  knowledge_scope_id?: string | null;
  question_field_name: string;
  answer_field_name: string;
  link_field_name: string;
  progress_field_name: string;
  status_field_name: string;
  module_field_name: string;
  registrant_field_name: string;
  feedback_result_field_name: string;
  feedback_final_answer_field_name: string;
  feedback_comment_field_name: string;
  confidence_field_name: string;
  hit_count_field_name: string;
  support_owner_rules: SupportIssueOwnerRule[];
  fallback_support_yht_user_id: string;
  digest_enabled: boolean;
  digest_recipient_emails: string[];
  case_review_enabled: boolean;
  created_at: string;
  updated_at: string;
  last_run_at?: string | null;
  next_run_at?: string | null;
  last_digest_at?: string | null;
  next_digest_at?: string | null;
  last_run_status?: SupportIssueRunStatus | null;
  last_run_summary?: string | null;
};

export type SupportIssueOwnerRule = {
  module_value: string;
  yht_user_id: string;
};

export type SupportIssueRun = {
  id: string;
  agent_id: string;
  status: SupportIssueRunStatus;
  started_at: string;
  ended_at?: string | null;
  fetched_row_count: number;
  processed_row_count: number;
  generated_count: number;
  manual_review_count: number;
  no_hit_count: number;
  failed_count: number;
  summary: string;
  error_message?: string | null;
  row_results: SupportIssueRowResult[];
};

export type SupportIssueCategoryStat = {
  category: string;
  count: number;
};

export type SupportIssueInsights = {
  agent_id: string;
  sample_run_count: number;
  total_processed_count: number;
  generated_count: number;
  manual_review_count: number;
  no_hit_count: number;
  failed_count: number;
  acceptance_count: number;
  revised_acceptance_count: number;
  rejected_count: number;
  pending_confirm_count: number;
  acceptance_rate: number;
  rejection_rate: number;
  low_confidence_rate: number;
  no_hit_rate: number;
  manual_rewrite_rate: number;
  top_categories: SupportIssueCategoryStat[];
  optimization_suggestions: string[];
};

export type SupportIssueFeedbackFact = {
  id: string;
  agent_id: string;
  record_id: string;
  question: string;
  progress_value: string;
  ai_solution: string;
  related_links: string[];
  feedback_result: string;
  feedback_final_answer: string;
  feedback_comment: string;
  confidence_score: number;
  retrieval_hit_count: number;
  question_category: string;
  source_bitable_url: string;
  created_at: string;
  updated_at: string;
  last_synced_at: string;
};

export type SupportIssueFeedbackSyncResponse = {
  agent_id: string;
  synced_row_count: number;
  fact_upsert_count: number;
  history_appended_count: number;
  candidate_created_count: number;
  candidate_updated_count: number;
  summary: string;
};

export type SupportIssueCaseCandidate = {
  id: string;
  agent_id: string;
  record_id: string;
  status: SupportIssueCaseCandidateStatus;
  question: string;
  ai_draft: string;
  feedback_result: string;
  final_solution: string;
  feedback_comment: string;
  confidence_score: number;
  retrieval_hit_count: number;
  question_category: string;
  related_links: string[];
  source_bitable_url: string;
  review_comment: string;
  knowledge_document_id?: string | null;
  approved_at?: string | null;
  approved_by?: string | null;
  created_at: string;
  updated_at: string;
};

export type SupportIssueDigestRun = {
  id: string;
  agent_id: string;
  status: SupportIssueDigestRunStatus;
  trigger_source: "manual" | "scheduled";
  started_at: string;
  ended_at?: string | null;
  period_start: string;
  period_end: string;
  recipient_emails: string[];
  email_sent: boolean;
  email_subject: string;
  summary: string;
  error_message?: string | null;
  total_processed_count: number;
  generated_count: number;
  manual_review_count: number;
  no_hit_count: number;
  failed_count: number;
  acceptance_count: number;
  revised_acceptance_count: number;
  rejected_count: number;
  acceptance_rate: number;
  rejection_rate: number;
  low_confidence_rate: number;
  no_hit_rate: number;
  manual_rewrite_rate: number;
  top_categories: SupportIssueCategoryStat[];
  top_no_hit_topics: string[];
  highlight_samples: string[];
  knowledge_gap_suggestions: string[];
  new_candidate_count: number;
  approved_candidate_count: number;
};


/** SSE 事件：前端用它接住后端流式返回的每个阶段更新。 */
export type SseEvent = {
  event: string;
  data: Record<string, unknown>;
};

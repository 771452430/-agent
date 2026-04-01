/**
 * 前端 API 封装层。
 *
 * 页面和组件尽量不要直接手写 `fetch` 细节，而是统一通过这里调用后端。
 * 这样 URL、请求方法、上传序列化、SSE 解析、错误处理都能集中维护。
 */
import type {
  AgentConfig,
  AgentRunResponse,
  Catalog,
  FeishuBitableFieldsResponse,
  FeishuBitablePendingAnalysisResponse,
  FeishuBitablePreviewResponse,
  FeishuBitableValidationResponse,
  FeishuBitableWriteValidationResponse,
  FeishuSettings,
  GitLabImportSettings,
  GitLabTreeImportResponse,
  KnowledgeDeleteResponse,
  KnowledgeDocument,
  KnowledgeTreeNode,
  KnowledgeTreeNodeDetail,
  KnowledgeTreeResponse,
  MailSettings,
  MailTestRequest,
  MailTestResponse,
  ModelConfig,
  ProviderConfig,
  ProviderTestResponse,
  RetrievalResult,
  ScopeType,
  SupportIssueAgentConfig,
  SupportIssueCaseCandidate,
  SupportIssueDigestRun,
  SupportIssueFeedbackSyncResponse,
  SupportIssueInsights,
  SupportIssueRun,
  SseEvent,
  ThreadState,
  ThreadSummary,
  UpdateMailSettingsRequest,
  UpdateProviderRequest,
  WatcherAgentConfig,
  WatcherFetchTestResponse,
  WatcherRun
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";
const SSE_SEPARATOR = String.fromCharCode(10, 10);
const SSE_LINE_BREAK = String.fromCharCode(10);

/** 统一解析 JSON 响应，并把 HTTP 错误转换成前端可见异常。 */
async function parseJson<T>(response: Response): Promise<T> {
  if (response.ok === false) {
    throw new Error(await response.text());
  }
  return (await response.json()) as T;
}

/** 把浏览器里的文件对象转成 Base64，便于通过 JSON 上传。 */
function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result ?? "");
      resolve(result.split(",")[1] ?? "");
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

// -----------------------------
// Catalog / Provider / 全局设置
// -----------------------------
export async function getCatalog(): Promise<Catalog> {
  const response = await fetch(API_BASE + "/api/catalog", { cache: "no-store" });
  return parseJson<Catalog>(response);
}

export async function listProviders(): Promise<ProviderConfig[]> {
  const response = await fetch(API_BASE + "/api/settings/providers", { cache: "no-store" });
  return parseJson<ProviderConfig[]>(response);
}

export async function getProvider(providerId: string): Promise<ProviderConfig> {
  const response = await fetch(API_BASE + "/api/settings/providers/" + providerId, { cache: "no-store" });
  return parseJson<ProviderConfig>(response);
}

export async function updateProvider(providerId: string, input: UpdateProviderRequest): Promise<ProviderConfig> {
  const response = await fetch(API_BASE + "/api/settings/providers/" + providerId, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return parseJson<ProviderConfig>(response);
}

export async function testProvider(providerId: string, input: UpdateProviderRequest): Promise<ProviderTestResponse> {
  const response = await fetch(API_BASE + "/api/settings/providers/" + providerId + "/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return parseJson<ProviderTestResponse>(response);
}

export async function getMailSettings(): Promise<MailSettings> {
  const response = await fetch(API_BASE + "/api/settings/mail", { cache: "no-store" });
  return parseJson<MailSettings>(response);
}

export async function updateMailSettings(input: UpdateMailSettingsRequest): Promise<MailSettings> {
  const response = await fetch(API_BASE + "/api/settings/mail", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return parseJson<MailSettings>(response);
}

export async function testMailSettings(input: MailTestRequest): Promise<MailTestResponse> {
  const response = await fetch(API_BASE + "/api/settings/mail/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return parseJson<MailTestResponse>(response);
}

export async function getFeishuSettings(): Promise<FeishuSettings> {
  const response = await fetch(API_BASE + "/api/settings/feishu", { cache: "no-store" });
  return parseJson<FeishuSettings>(response);
}

export async function updateFeishuSettings(input: {
  app_id?: string;
  app_secret?: string;
}): Promise<FeishuSettings> {
  const response = await fetch(API_BASE + "/api/settings/feishu", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return parseJson<FeishuSettings>(response);
}

export async function getGitLabImportSettings(): Promise<GitLabImportSettings> {
  const response = await fetch(API_BASE + "/api/settings/gitlab-import", { cache: "no-store" });
  return parseJson<GitLabImportSettings>(response);
}

export async function updateGitLabImportSettings(input: {
  token?: string;
  clear_token?: boolean;
  allowed_hosts?: string[];
}): Promise<GitLabImportSettings> {
  const response = await fetch(API_BASE + "/api/settings/gitlab-import", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return parseJson<GitLabImportSettings>(response);
}

export async function validateSupportAgentBitable(input: {
  feishu_bitable_url: string;
}): Promise<FeishuBitableValidationResponse> {
  const response = await fetch(API_BASE + "/api/support-agents/validate-bitable", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return parseJson<FeishuBitableValidationResponse>(response);
}

export async function previewSupportAgentBitable(input: {
  feishu_bitable_url: string;
}): Promise<FeishuBitablePreviewResponse> {
  const response = await fetch(API_BASE + "/api/support-agents/preview-bitable", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return parseJson<FeishuBitablePreviewResponse>(response);
}

export async function listSupportAgentBitableFields(input: {
  feishu_bitable_url: string;
}): Promise<FeishuBitableFieldsResponse> {
  const response = await fetch(API_BASE + "/api/support-agents/fields-bitable", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return parseJson<FeishuBitableFieldsResponse>(response);
}

export async function listSupportAgentPendingAnalysisRows(input: {
  feishu_bitable_url: string;
  progress_field_name?: string;
}): Promise<FeishuBitablePendingAnalysisResponse> {
  const response = await fetch(API_BASE + "/api/support-agents/pending-analysis-bitable", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return parseJson<FeishuBitablePendingAnalysisResponse>(response);
}

export async function validateSupportAgentBitableWrite(input: {
  feishu_bitable_url: string;
  question_field_name: string;
  answer_field_name: string;
  status_field_name: string;
}): Promise<FeishuBitableWriteValidationResponse> {
  const response = await fetch(API_BASE + "/api/support-agents/validate-bitable-write", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return parseJson<FeishuBitableWriteValidationResponse>(response);
}

export async function listThreads(): Promise<ThreadSummary[]> {
  const response = await fetch(API_BASE + "/api/threads", { cache: "no-store" });
  return parseJson<ThreadSummary[]>(response);
}

export async function createThread(input?: {
  title?: string;
  model_config?: ModelConfig;
  enabled_skills?: string[];
}): Promise<{ thread_id: string; title: string }> {
  const response = await fetch(API_BASE + "/api/threads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input ?? {})
  });
  return parseJson<{ thread_id: string; title: string }>(response);
}

export async function getThread(threadId: string): Promise<ThreadState> {
  const response = await fetch(API_BASE + "/api/threads/" + threadId, { cache: "no-store" });
  return parseJson<ThreadState>(response);
}

export async function streamMessage(
  threadId: string,
  payload: {
    content: string;
    model_config: ModelConfig;
    enabled_skills: string[];
  },
  onEvent: (event: SseEvent) => void
): Promise<void> {
  const response = await fetch(API_BASE + "/api/threads/" + threadId + "/messages", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  if (response.ok === false || response.body == null) {
    throw new Error(await response.text());
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf(SSE_SEPARATOR);
    while (boundary >= 0) {
      const rawEvent = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + SSE_SEPARATOR.length);
      const parsed = parseSseEvent(rawEvent);
      if (parsed == null) {
        boundary = buffer.indexOf(SSE_SEPARATOR);
        continue;
      }
      onEvent(parsed);
      boundary = buffer.indexOf(SSE_SEPARATOR);
    }
  }
}

function parseSseEvent(raw: string): SseEvent | null {
  const lines = raw.split(SSE_LINE_BREAK);
  const eventLine = lines.find((line) => line.startsWith("event:"));
  const dataLine = lines.find((line) => line.startsWith("data:"));
  if (eventLine == null || dataLine == null) return null;
  return {
    event: eventLine.replace("event:", "").trim(),
    data: JSON.parse(dataLine.replace("data:", "").trim())
  };
}

// -----------------------------
// 知识库与检索模式
// -----------------------------
export async function listDocuments(): Promise<KnowledgeDocument[]> {
  const response = await fetch(API_BASE + "/api/knowledge/documents", { cache: "no-store" });
  return parseJson<KnowledgeDocument[]>(response);
}

export async function uploadDocument(file: File): Promise<KnowledgeDocument> {
  const contentBase64 = await fileToBase64(file);
  const response = await fetch(API_BASE + "/api/knowledge/documents", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file_name: file.name, content_base64: contentBase64 })
  });
  return parseJson<KnowledgeDocument>(response);
}

export async function getKnowledgeTree(): Promise<KnowledgeTreeResponse> {
  const response = await fetch(API_BASE + "/api/knowledge/tree", { cache: "no-store" });
  return parseJson<KnowledgeTreeResponse>(response);
}

export async function getKnowledgeNodeDetail(nodeId: string): Promise<KnowledgeTreeNodeDetail> {
  const response = await fetch(API_BASE + "/api/knowledge/tree/" + nodeId, { cache: "no-store" });
  return parseJson<KnowledgeTreeNodeDetail>(response);
}

export async function createKnowledgeNode(input: {
  name: string;
  parent_id?: string | null;
}): Promise<KnowledgeTreeNode> {
  const response = await fetch(API_BASE + "/api/knowledge/tree/nodes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return parseJson<KnowledgeTreeNode>(response);
}

export async function uploadDirectory(files: File[], parentNodeId?: string | null): Promise<KnowledgeDocument[]> {
  const encodedFiles = await Promise.all(
    files.map(async (file) => ({
      file_name: file.name,
      relative_path: file.webkitRelativePath || file.name,
      content_base64: await fileToBase64(file)
    }))
  );
  const response = await fetch(API_BASE + "/api/knowledge/tree/upload-directory", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parent_node_id: parentNodeId ?? null, files: encodedFiles })
  });
  return parseJson<KnowledgeDocument[]>(response);
}

export async function importGitLabTree(input: {
  tree_url: string;
  parent_node_id?: string | null;
}): Promise<GitLabTreeImportResponse> {
  const response = await fetch(API_BASE + "/api/knowledge/tree/import-gitlab", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return parseJson<GitLabTreeImportResponse>(response);
}

export async function uploadDocumentToNode(nodeId: string, file: File): Promise<KnowledgeDocument> {
  const response = await fetch(API_BASE + "/api/knowledge/tree/" + nodeId + "/documents", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      file_name: file.name,
      relative_path: file.webkitRelativePath || file.name,
      content_base64: await fileToBase64(file)
    })
  });
  return parseJson<KnowledgeDocument>(response);
}

export async function deleteKnowledgeNode(nodeId: string): Promise<KnowledgeDeleteResponse> {
  const response = await fetch(API_BASE + "/api/knowledge/tree/" + nodeId, {
    method: "DELETE"
  });
  return parseJson<KnowledgeDeleteResponse>(response);
}

export async function deleteKnowledgeDocument(documentId: string): Promise<KnowledgeDeleteResponse> {
  const response = await fetch(API_BASE + "/api/knowledge/documents/" + documentId, {
    method: "DELETE"
  });
  return parseJson<KnowledgeDeleteResponse>(response);
}

export async function updateKnowledgeDocument(
  documentId: string,
  input: {
    external_url?: string | null;
  }
): Promise<KnowledgeDocument> {
  const response = await fetch(API_BASE + "/api/knowledge/documents/" + documentId, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return parseJson<KnowledgeDocument>(response);
}

export async function queryRetrieval(input: {
  query: string;
  scope_type: ScopeType;
  scope_id?: string | null;
  model_config?: ModelConfig;
}): Promise<RetrievalResult> {
  const response = await fetch(API_BASE + "/api/retrieval/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return parseJson<RetrievalResult>(response);
}

// -----------------------------
// 配置型 Agent
// -----------------------------
export async function listAgents(): Promise<AgentConfig[]> {
  const response = await fetch(API_BASE + "/api/agents", { cache: "no-store" });
  return parseJson<AgentConfig[]>(response);
}

export async function getAgent(agentId: string): Promise<AgentConfig> {
  const response = await fetch(API_BASE + "/api/agents/" + agentId, { cache: "no-store" });
  return parseJson<AgentConfig>(response);
}

export async function createAgent(input: {
  name: string;
  description: string;
  system_prompt: string;
  model_config: ModelConfig;
  enabled_skills: string[];
  knowledge_scope_type: ScopeType;
  knowledge_scope_id?: string | null;
}): Promise<AgentConfig> {
  const response = await fetch(API_BASE + "/api/agents", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return parseJson<AgentConfig>(response);
}

export async function updateAgent(
  agentId: string,
  input: Partial<{
    name: string;
    description: string;
    system_prompt: string;
    model_config: ModelConfig;
    enabled_skills: string[];
    knowledge_scope_type: ScopeType;
    knowledge_scope_id?: string | null;
  }>
): Promise<AgentConfig> {
  const response = await fetch(API_BASE + "/api/agents/" + agentId, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return parseJson<AgentConfig>(response);
}

export async function runAgent(agentId: string, content: string): Promise<AgentRunResponse> {
  const response = await fetch(API_BASE + "/api/agents/" + agentId + "/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content })
  });
  return parseJson<AgentRunResponse>(response);
}

// -----------------------------
// 巡检 Agent
// -----------------------------
export async function listWatchers(): Promise<WatcherAgentConfig[]> {
  const response = await fetch(API_BASE + "/api/watchers", { cache: "no-store" });
  return parseJson<WatcherAgentConfig[]>(response);
}

export async function getWatcher(watcherId: string): Promise<WatcherAgentConfig> {
  const response = await fetch(API_BASE + "/api/watchers/" + watcherId, { cache: "no-store" });
  return parseJson<WatcherAgentConfig>(response);
}

export async function createWatcher(input: {
  name: string;
  description: string;
  dashboard_url: string;
  request_method: "GET" | "POST";
  request_headers: Record<string, string>;
  request_body_json?: Record<string, unknown> | null;
  poll_interval_minutes: number;
  sender_email: string;
  recipient_emails: string[];
  model_config: ModelConfig;
  enabled: boolean;
  owner_rules: Array<{
    assignee_code: string;
    services: string[];
    modules: string[];
    keywords: string[];
    assignment_payload_template?: Record<string, unknown> | null;
  }>;
}): Promise<WatcherAgentConfig> {
  const response = await fetch(API_BASE + "/api/watchers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return parseJson<WatcherAgentConfig>(response);
}

export async function updateWatcher(
  watcherId: string,
  input: Partial<{
    name: string;
    description: string;
    dashboard_url: string;
    request_method: "GET" | "POST";
    request_headers: Record<string, string>;
    request_body_json?: Record<string, unknown> | null;
    poll_interval_minutes: number;
    sender_email: string;
    recipient_emails: string[];
    model_config: ModelConfig;
    enabled: boolean;
    owner_rules: Array<{
      assignee_code: string;
      services: string[];
      modules: string[];
      keywords: string[];
      assignment_payload_template?: Record<string, unknown> | null;
    }>;
  }>
): Promise<WatcherAgentConfig> {
  const response = await fetch(API_BASE + "/api/watchers/" + watcherId, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return parseJson<WatcherAgentConfig>(response);
}

export async function testWatcherFetch(input: {
  dashboard_url: string;
  request_method: "GET" | "POST";
  request_headers: Record<string, string>;
  request_body_json?: Record<string, unknown> | null;
}): Promise<WatcherFetchTestResponse> {
  const response = await fetch(API_BASE + "/api/watchers/test-fetch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return parseJson<WatcherFetchTestResponse>(response);
}

export async function runWatcher(
  watcherId: string,
  input?: {
    assign_current_list?: boolean;
  }
): Promise<WatcherRun> {
  const response = await fetch(API_BASE + "/api/watchers/" + watcherId + "/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input ?? {})
  });
  return parseJson<WatcherRun>(response);
}

export async function listWatcherRuns(watcherId: string): Promise<WatcherRun[]> {
  const response = await fetch(API_BASE + "/api/watchers/" + watcherId + "/runs", { cache: "no-store" });
  return parseJson<WatcherRun[]>(response);
}

// -----------------------------
// 支持问题 Agent
// -----------------------------
export async function listSupportAgents(): Promise<SupportIssueAgentConfig[]> {
  const response = await fetch(API_BASE + "/api/support-agents", { cache: "no-store" });
  return parseJson<SupportIssueAgentConfig[]>(response);
}

export async function getSupportAgent(agentId: string): Promise<SupportIssueAgentConfig> {
  const response = await fetch(API_BASE + "/api/support-agents/" + agentId, { cache: "no-store" });
  return parseJson<SupportIssueAgentConfig>(response);
}

export async function createSupportAgent(input: {
  name: string;
  description: string;
  enabled: boolean;
  poll_interval_minutes: number;
  feishu_bitable_url: string;
  model_config: ModelConfig;
  knowledge_scope_type: ScopeType;
  knowledge_scope_id?: string | null;
  question_field_name: string;
  answer_field_name: string;
  link_field_name: string;
  progress_field_name: string;
  status_field_name: string;
  feedback_result_field_name: string;
  feedback_final_answer_field_name: string;
  feedback_comment_field_name: string;
  confidence_field_name: string;
  hit_count_field_name: string;
  digest_enabled: boolean;
  digest_recipient_emails: string[];
  case_review_enabled: boolean;
}): Promise<SupportIssueAgentConfig> {
  const response = await fetch(API_BASE + "/api/support-agents", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return parseJson<SupportIssueAgentConfig>(response);
}

export async function updateSupportAgent(
  agentId: string,
  input: Partial<{
    name: string;
    description: string;
    enabled: boolean;
    poll_interval_minutes: number;
    feishu_bitable_url: string;
    model_config: ModelConfig;
    knowledge_scope_type: ScopeType;
    knowledge_scope_id?: string | null;
    question_field_name: string;
    answer_field_name: string;
    link_field_name: string;
    progress_field_name: string;
    status_field_name: string;
    feedback_result_field_name: string;
    feedback_final_answer_field_name: string;
    feedback_comment_field_name: string;
    confidence_field_name: string;
    hit_count_field_name: string;
    digest_enabled: boolean;
    digest_recipient_emails: string[];
    case_review_enabled: boolean;
  }>
): Promise<SupportIssueAgentConfig> {
  const response = await fetch(API_BASE + "/api/support-agents/" + agentId, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return parseJson<SupportIssueAgentConfig>(response);
}

export async function runSupportAgent(agentId: string): Promise<SupportIssueRun> {
  const response = await fetch(API_BASE + "/api/support-agents/" + agentId + "/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({})
  });
  return parseJson<SupportIssueRun>(response);
}

export async function listSupportAgentRuns(agentId: string): Promise<SupportIssueRun[]> {
  const response = await fetch(API_BASE + "/api/support-agents/" + agentId + "/runs", { cache: "no-store" });
  return parseJson<SupportIssueRun[]>(response);
}

export async function getSupportAgentInsights(agentId: string): Promise<SupportIssueInsights> {
  const response = await fetch(API_BASE + "/api/support-agents/" + agentId + "/insights", { cache: "no-store" });
  return parseJson<SupportIssueInsights>(response);
}

export async function syncSupportAgentFeedback(agentId: string): Promise<SupportIssueFeedbackSyncResponse> {
  const response = await fetch(API_BASE + "/api/support-agents/" + agentId + "/sync-feedback", {
    method: "POST"
  });
  return parseJson<SupportIssueFeedbackSyncResponse>(response);
}

export async function listSupportAgentCaseCandidates(
  agentId: string,
  filters?: {
    status?: "pending_review" | "approved";
    category?: string;
    keyword?: string;
  }
): Promise<SupportIssueCaseCandidate[]> {
  const query = new URLSearchParams();
  if ((filters?.status || "").trim() !== "") {
    query.set("status", (filters?.status || "").trim());
  }
  if ((filters?.category || "").trim() !== "") {
    query.set("category", (filters?.category || "").trim());
  }
  if ((filters?.keyword || "").trim() !== "") {
    query.set("keyword", (filters?.keyword || "").trim());
  }
  const response = await fetch(
    API_BASE + "/api/support-agents/" + agentId + "/case-candidates" + (query.size > 0 ? "?" + query.toString() : ""),
    {
      cache: "no-store"
    }
  );
  return parseJson<SupportIssueCaseCandidate[]>(response);
}

export async function reviewSupportCaseCandidate(
  candidateId: string,
  input: {
    action: "save_edit" | "approve_and_publish";
    reviewer_name: string;
    review_comment?: string;
    final_solution?: string;
    feedback_comment?: string;
    sync_to_feishu?: boolean;
  }
): Promise<SupportIssueCaseCandidate> {
  const response = await fetch(API_BASE + "/api/support-case-candidates/" + candidateId, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return parseJson<SupportIssueCaseCandidate>(response);
}

export async function runSupportAgentDigest(agentId: string): Promise<SupportIssueDigestRun> {
  const response = await fetch(API_BASE + "/api/support-agents/" + agentId + "/digest", {
    method: "POST"
  });
  return parseJson<SupportIssueDigestRun>(response);
}

export async function listSupportAgentDigestRuns(agentId: string): Promise<SupportIssueDigestRun[]> {
  const response = await fetch(API_BASE + "/api/support-agents/" + agentId + "/digest-runs", {
    cache: "no-store"
  });
  return parseJson<SupportIssueDigestRun[]>(response);
}

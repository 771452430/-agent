"use client";

/**
 * 支持问题 Agent 工作区。
 *
 * 这里汇总了飞书表格接入、字段映射、RAG 生成、运行记录、统计洞察和案例沉淀等能力，
 * 是前端里最复杂的业务页面之一。阅读时建议按“配置 -> 验证 -> 运行 -> 查看结果”顺序理解。
 */
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import {
  createSupportAgent,
  getKnowledgeTree,
  getSupportAgent,
  listSupportAgentCaseCandidates,
  listSupportAgentDigestRuns,
  getSupportAgentInsights,
  listSupportAgentBitableFields,
  listSupportAgentPendingAnalysisRows,
  listSupportAgentRuns,
  listSupportAgents,
  previewSupportAgentBitable,
  runSupportAgent,
  runSupportAgentDigest,
  syncSupportAgentFeedback,
  updateSupportAgent,
  validateSupportAgentBitable,
  validateSupportAgentBitableWrite
} from "../lib/api";
import type {
  FeishuBitableFieldInfo,
  FeishuBitableFieldsResponse,
  FeishuBitablePendingAnalysisResponse,
  FeishuBitablePreviewResponse,
  FeishuBitableValidationResponse,
  FeishuBitableWriteValidationResponse,
  KnowledgeTreeNode,
  KnowledgeTreeResponse,
  ModelConfig,
  ScopeType,
  SupportIssueAgentConfig,
  SupportIssueCaseCandidate,
  SupportIssueDigestRun,
  SupportIssueFeedbackSyncResponse,
  SupportIssueInsights,
  SupportIssueOwnerRule,
  SupportIssueRun
} from "../lib/types";
import { ModelSelector } from "./model-selector";
import { useModelSettings } from "./model-settings-provider";

/** 支持问题 Agent 默认采用学习模式模型。 */
const DEFAULT_MODEL: ModelConfig = {
  mode: "learning",
  provider: "mock",
  model: "learning-mode",
  temperature: 0.2,
  max_tokens: 1024
};

type SupportIssueAgentFormState = {
  id?: string;
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
  digest_recipient_emails_text: string;
  case_review_enabled: boolean;
  last_run_at?: string | null;
  next_run_at?: string | null;
  last_digest_at?: string | null;
  next_digest_at?: string | null;
};

type ValidationState = {
  validatedInput: string;
  result: FeishuBitableValidationResponse;
};

type PreviewState = {
  previewInput: string;
  result: FeishuBitablePreviewResponse;
};

type FieldsState = {
  fieldsInput: string;
  result: FeishuBitableFieldsResponse;
};

type WriteValidationState = {
  writeInput: string;
  result: FeishuBitableWriteValidationResponse;
};

type PendingAnalysisState = {
  filterInput: string;
  result: FeishuBitablePendingAnalysisResponse;
};

/** 把时间戳格式化成适合界面展示的中文时间。 */
function formatDate(value?: string | null) {
  if (value == null || value === "") return "-";
  return new Date(value).toLocaleString("zh-CN");
}

/** 把知识树拍平成线性选项，便于表单选择知识范围。 */
function flattenTree(node: KnowledgeTreeNode): Array<{ id: string; label: string }> {
  const current = [{ id: node.id, label: node.path + " · " + node.name }];
  return current.concat(node.children.flatMap(flattenTree));
}

/** 构造新建支持问题 Agent 时的默认表单值。 */
function buildEmptyForm(): SupportIssueAgentFormState {
  return {
    name: "新的支持问题 Agent",
    description: "",
    enabled: true,
    poll_interval_minutes: 30,
    feishu_bitable_url: "",
    model_config: DEFAULT_MODEL,
    knowledge_scope_type: "global",
    knowledge_scope_id: null,
    question_field_name: "问题",
    answer_field_name: "AI解决方案",
    link_field_name: "相关文档链接",
    progress_field_name: "回复进度",
    status_field_name: "处理状态",
    module_field_name: "负责模块",
    registrant_field_name: "登记人",
    feedback_result_field_name: "人工处理结果",
    feedback_final_answer_field_name: "人工最终方案",
    feedback_comment_field_name: "反馈备注",
    confidence_field_name: "AI置信度",
    hit_count_field_name: "命中知识数",
    support_owner_rules: [],
    fallback_support_yht_user_id: "",
    digest_enabled: false,
    digest_recipient_emails_text: "",
    case_review_enabled: true,
    last_run_at: null,
    next_run_at: null,
    last_digest_at: null,
    next_digest_at: null
  };
}

/** 把小数比率转换成百分比文本。 */
function formatPercent(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}

/** 把逗号/换行分隔文本解析成邮箱或字段列表，并自动去重。 */
function parseCommaList(value: string) {
  return value
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter((item, index, list) => item !== "" && list.indexOf(item) === index);
}

function buildEmptyOwnerRule(): SupportIssueOwnerRule {
  return {
    module_value: "",
    yht_user_id: ""
  };
}

/** 构造统一的校验失败对象，方便前端复用同一套展示逻辑。 */
function buildValidationFailure(message: string): FeishuBitableValidationResponse {
  return {
    ok: false,
    message,
    normalized_url: "",
    parsed_app_token: "",
    parsed_table_id: "",
    parsed_view_id: null
  };
}

/** 把不同飞书校验接口的返回结果收敛成统一的基础展示结构。 */
function toValidationResult(
  result:
    | FeishuBitableValidationResponse
    | FeishuBitableFieldsResponse
    | FeishuBitablePendingAnalysisResponse
    | FeishuBitablePreviewResponse
    | FeishuBitableWriteValidationResponse
): FeishuBitableValidationResponse {
  return {
    ok: result.ok,
    message: result.message,
    normalized_url: result.normalized_url,
    parsed_app_token: result.parsed_app_token,
    parsed_table_id: result.parsed_table_id,
    parsed_view_id: result.parsed_view_id ?? null
  };
}

export function SupportIssueAgentsWorkspace() {
  const { feishuSettings, openFeishuSettings, openModelSettings, validateModelConfig } = useModelSettings();
  const [tree, setTree] = useState<KnowledgeTreeResponse | null>(null);
  const [agents, setAgents] = useState<SupportIssueAgentConfig[]>([]);
  const [runs, setRuns] = useState<SupportIssueRun[]>([]);
  const [caseCandidates, setCaseCandidates] = useState<SupportIssueCaseCandidate[]>([]);
  const [digestRuns, setDigestRuns] = useState<SupportIssueDigestRun[]>([]);
  const [insights, setInsights] = useState<SupportIssueInsights | null>(null);
  const [feedbackSyncResult, setFeedbackSyncResult] = useState<SupportIssueFeedbackSyncResponse | null>(null);
  const [form, setForm] = useState<SupportIssueAgentFormState>(buildEmptyForm());
  const [validationState, setValidationState] = useState<ValidationState | null>(null);
  const [fieldsState, setFieldsState] = useState<FieldsState | null>(null);
  const [previewState, setPreviewState] = useState<PreviewState | null>(null);
  const [writeValidationState, setWriteValidationState] = useState<WriteValidationState | null>(null);
  const [pendingAnalysisState, setPendingAnalysisState] = useState<PendingAnalysisState | null>(null);
  const [error, setError] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [isValidating, setIsValidating] = useState(false);
  const [isLoadingFields, setIsLoadingFields] = useState(false);
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [isWriteValidating, setIsWriteValidating] = useState(false);
  const [isFilteringPendingAnalysis, setIsFilteringPendingAnalysis] = useState(false);
  const [isSyncingFeedback, setIsSyncingFeedback] = useState(false);
  const [isDigesting, setIsDigesting] = useState(false);
  const [isBootstrapping, setIsBootstrapping] = useState(true);
  const modelValidation = validateModelConfig(form.model_config);

  const treeOptions = useMemo(() => {
    if (tree == null) return [];
    return flattenTree(tree.root);
  }, [tree]);

  const normalizedBitableUrl = form.feishu_bitable_url.trim();
  const isValidationPassed =
    validationState?.result.ok === true && validationState.validatedInput === normalizedBitableUrl;
  const isActionBusy =
    isSaving ||
    isRunning ||
    isValidating ||
    isLoadingFields ||
    isPreviewing ||
    isWriteValidating ||
    isFilteringPendingAnalysis ||
    isSyncingFeedback ||
    isDigesting;
  const runBlockedReason =
    !feishuSettings?.configured
      ? "请先到设置 -> 飞书设置保存 App ID 和 App Secret。"
      : normalizedBitableUrl === ""
        ? "请先填写飞书多维表格地址。"
        : !isValidationPassed
          ? "请先验证地址通过后再立即运行。"
          : "";
  const previewBlockedReason =
    !feishuSettings?.configured
      ? "请先到设置 -> 飞书设置保存 App ID 和 App Secret。"
      : normalizedBitableUrl === ""
        ? "请先填写飞书多维表格地址。"
        : "";
  const fieldsBlockedReason = previewBlockedReason;
  const pendingAnalysisBlockedReason = previewBlockedReason;
  const writeValidationBlockedReason =
    previewBlockedReason !== ""
      ? previewBlockedReason
      : "";

  function resetDiagnostics() {
    setValidationState(null);
    setFieldsState(null);
    setPreviewState(null);
    setWriteValidationState(null);
    setPendingAnalysisState(null);
  }

  function syncValidationState(
    validatedInput: string,
    result:
      | FeishuBitableValidationResponse
      | FeishuBitableFieldsResponse
      | FeishuBitablePendingAnalysisResponse
      | FeishuBitablePreviewResponse
      | FeishuBitableWriteValidationResponse
  ) {
    if (!result.ok) return;
    const normalizedUrl = result.normalized_url || validatedInput;
    setForm((current) => ({ ...current, feishu_bitable_url: normalizedUrl }));
    setValidationState({
      validatedInput: normalizedUrl,
      result: toValidationResult(result)
    });
  }

  async function selectAgent(agentId: string, options?: { resetValidation?: boolean }) {
    const nextOptions = { resetValidation: true, ...options };
    const [detail, nextRuns, nextInsights, nextCaseCandidates, nextDigestRuns] = await Promise.all([
      getSupportAgent(agentId),
      listSupportAgentRuns(agentId),
      getSupportAgentInsights(agentId),
      listSupportAgentCaseCandidates(agentId),
      listSupportAgentDigestRuns(agentId)
    ]);
    setForm({
      id: detail.id,
      name: detail.name,
      description: detail.description,
      enabled: detail.enabled,
      poll_interval_minutes: detail.poll_interval_minutes,
      feishu_bitable_url: detail.feishu_bitable_url,
      model_config: detail.model_config,
      knowledge_scope_type: detail.knowledge_scope_type,
      knowledge_scope_id: detail.knowledge_scope_id ?? null,
      question_field_name: detail.question_field_name,
      answer_field_name: detail.answer_field_name,
      link_field_name: detail.link_field_name,
      progress_field_name: detail.progress_field_name,
      status_field_name: detail.status_field_name,
      module_field_name: detail.module_field_name,
      registrant_field_name: detail.registrant_field_name,
      feedback_result_field_name: detail.feedback_result_field_name,
      feedback_final_answer_field_name: detail.feedback_final_answer_field_name,
      feedback_comment_field_name: detail.feedback_comment_field_name,
      confidence_field_name: detail.confidence_field_name,
      hit_count_field_name: detail.hit_count_field_name,
      support_owner_rules: detail.support_owner_rules,
      fallback_support_yht_user_id: detail.fallback_support_yht_user_id,
      digest_enabled: detail.digest_enabled,
      digest_recipient_emails_text: detail.digest_recipient_emails.join(", "),
      case_review_enabled: detail.case_review_enabled,
      last_run_at: detail.last_run_at ?? null,
      next_run_at: detail.next_run_at ?? null,
      last_digest_at: detail.last_digest_at ?? null,
      next_digest_at: detail.next_digest_at ?? null
    });
    setRuns(nextRuns);
    setInsights(nextInsights);
    setCaseCandidates(nextCaseCandidates);
    setDigestRuns(nextDigestRuns);
    setFeedbackSyncResult(null);
    if (nextOptions.resetValidation) {
      resetDiagnostics();
    }
  }

  async function bootstrap() {
    setIsBootstrapping(true);
    setError("");
    try {
      const [treeData, agentsData] = await Promise.all([getKnowledgeTree(), listSupportAgents()]);
      setTree(treeData);
      setAgents(agentsData);
      if (agentsData.length > 0) {
        await selectAgent(agentsData[0].id);
      } else {
        setForm(buildEmptyForm());
        setRuns([]);
        setCaseCandidates([]);
        setDigestRuns([]);
        setInsights(null);
        setFeedbackSyncResult(null);
        resetDiagnostics();
      }
    } finally {
      setIsBootstrapping(false);
    }
  }

  async function refreshAgents(selectId?: string, options?: { resetValidation?: boolean }) {
    // 这个刷新入口负责统一同步：
    // Agent 列表、当前选中项、运行记录、案例候选、洞察面板。
    const nextOptions = { resetValidation: true, ...options };
    const nextAgents = await listSupportAgents();
    setAgents(nextAgents);
    const targetId = selectId ?? nextAgents[0]?.id ?? "";
    if (targetId !== "") {
      await selectAgent(targetId, nextOptions);
      return;
    }
    setForm(buildEmptyForm());
    setRuns([]);
    setCaseCandidates([]);
    setDigestRuns([]);
    setInsights(null);
    setFeedbackSyncResult(null);
    if (nextOptions.resetValidation) {
      resetDiagnostics();
    }
  }

  useEffect(() => {
    void bootstrap().catch((cause) => setError(String(cause)));
  }, []);

  useEffect(() => {
    // 只要飞书表格 URL 变了，之前基于旧地址拿到的验证/预览/字段结果
    // 就不再可信，因此这里会统一清空这些诊断信息。
    const validationChanged = validationState != null && validationState.validatedInput !== normalizedBitableUrl;
    const fieldsChanged = fieldsState != null && fieldsState.fieldsInput !== normalizedBitableUrl;
    const previewChanged = previewState != null && previewState.previewInput !== normalizedBitableUrl;
    const writeChanged = writeValidationState != null && writeValidationState.writeInput !== normalizedBitableUrl;
    const pendingAnalysisChanged = pendingAnalysisState != null && pendingAnalysisState.filterInput !== normalizedBitableUrl;
    if (validationChanged || fieldsChanged || previewChanged || writeChanged || pendingAnalysisChanged) {
      resetDiagnostics();
    }
  }, [normalizedBitableUrl, validationState, fieldsState, previewState, writeValidationState, pendingAnalysisState]);

  useEffect(() => {
    // 飞书全局配置变化后，之前缓存的校验结果也可能失效，
    // 因为新的 App ID / Secret 会对应新的访问权限上下文。
    if (
      validationState != null ||
      fieldsState != null ||
      previewState != null ||
      writeValidationState != null ||
      pendingAnalysisState != null
    ) {
      resetDiagnostics();
    }
  }, [feishuSettings?.configured, feishuSettings?.app_id, feishuSettings?.app_secret_masked]);

  async function handleValidateAddress() {
    // 第一步先验证“这个飞书地址本身能不能被正确解析”，
    // 只有地址通过了，后面的预览、字段发现、写回验证才有意义。
    const currentUrl = normalizedBitableUrl;
    if (currentUrl === "") {
      setValidationState({
        validatedInput: "",
        result: buildValidationFailure("请先填写飞书多维表格地址。")
      });
      return;
    }

    setIsValidating(true);
    setError("");
    try {
      const result = await validateSupportAgentBitable({ feishu_bitable_url: currentUrl });
      if (result.ok && result.normalized_url !== "") {
        setForm((current) => ({ ...current, feishu_bitable_url: result.normalized_url }));
        setValidationState({
          validatedInput: result.normalized_url,
          result
        });
      } else {
        setValidationState({
          validatedInput: currentUrl,
          result
        });
      }
    } catch (cause) {
      setValidationState({
        validatedInput: currentUrl,
        result: buildValidationFailure(String(cause))
      });
    } finally {
      setIsValidating(false);
    }
  }

  async function handlePreviewBitable() {
    // 预览用于回答两个问题：
    // 1. 当前凭据能否真正读到表；
    // 2. 表里的原始字段和样例数据长什么样。
    if (previewBlockedReason !== "") {
      setError(previewBlockedReason);
      if (!feishuSettings?.configured) {
        openFeishuSettings();
      }
      return;
    }

    setIsPreviewing(true);
    setError("");
    const currentUrl = normalizedBitableUrl;
    try {
      const result = await previewSupportAgentBitable({ feishu_bitable_url: currentUrl });
      setPreviewState({ previewInput: result.normalized_url || currentUrl, result });
      syncValidationState(currentUrl, result);
    } catch (cause) {
      setPreviewState({
        previewInput: currentUrl,
        result: {
          ok: false,
          message: String(cause),
          normalized_url: "",
          parsed_app_token: "",
          parsed_table_id: "",
          parsed_view_id: null,
          preview_rows: [],
          preview_count: 0,
          has_more: false
        }
      });
    } finally {
      setIsPreviewing(false);
    }
  }

  async function handleLoadFields() {
    // 字段发现会尽量走元数据接口；
    // 如果接口能力受限，再回退到预览数据里猜字段结构。
    if (fieldsBlockedReason !== "") {
      setError(fieldsBlockedReason);
      if (!feishuSettings?.configured) {
        openFeishuSettings();
      }
      return;
    }

    setIsLoadingFields(true);
    setError("");
    const currentUrl = normalizedBitableUrl;
    try {
      const result = await listSupportAgentBitableFields({ feishu_bitable_url: currentUrl });
      setFieldsState({ fieldsInput: result.normalized_url || currentUrl, result });
      syncValidationState(currentUrl, result);
    } catch (cause) {
      setFieldsState({
        fieldsInput: currentUrl,
        result: {
          ok: false,
          message: String(cause),
          normalized_url: "",
          parsed_app_token: "",
          parsed_table_id: "",
          parsed_view_id: null,
          fields: [],
          source: "metadata_api"
        }
      });
    } finally {
      setIsLoadingFields(false);
    }
  }

  async function handleWriteValidation() {
    // 写回验证是最接近“正式运行”的一次探测：
    // 它会模拟创建/更新/删除记录，确认当前字段映射和写权限是否可用。
    if (writeValidationBlockedReason !== "") {
      setError(writeValidationBlockedReason);
      if (!feishuSettings?.configured) {
        openFeishuSettings();
      }
      return;
    }

    setIsWriteValidating(true);
    setError("");
    const currentUrl = normalizedBitableUrl;
    try {
      const result = await validateSupportAgentBitableWrite({
        feishu_bitable_url: currentUrl,
        question_field_name: form.question_field_name.trim(),
        answer_field_name: form.answer_field_name.trim(),
        status_field_name: form.status_field_name.trim()
      });
      setWriteValidationState({ writeInput: result.normalized_url || currentUrl, result });
      syncValidationState(currentUrl, result);
    } catch (cause) {
      setWriteValidationState({
        writeInput: currentUrl,
        result: {
          ok: false,
          message: String(cause),
          normalized_url: "",
          parsed_app_token: "",
          parsed_table_id: "",
          parsed_view_id: null,
          created_record_id: null,
          updated_record_id: null,
          deleted_record_id: null,
          created_fields_preview: {},
          updated_fields_preview: {}
        }
      });
    } finally {
      setIsWriteValidating(false);
    }
  }

  async function handleFilterPendingAnalysis() {
    // 这个操作帮助你确认：
    // 配置的“待分析”筛选条件，是否真的能筛出预期的飞书记录。
    if (pendingAnalysisBlockedReason !== "") {
      setError(pendingAnalysisBlockedReason);
      if (!feishuSettings?.configured) {
        openFeishuSettings();
      }
      return;
    }

    setIsFilteringPendingAnalysis(true);
    setError("");
    const currentUrl = normalizedBitableUrl;
    try {
      const result = await listSupportAgentPendingAnalysisRows({
        feishu_bitable_url: currentUrl,
        progress_field_name: form.progress_field_name.trim() || "回复进度"
      });
      setPendingAnalysisState({ filterInput: result.normalized_url || currentUrl, result });
      syncValidationState(currentUrl, result);
    } catch (cause) {
      setPendingAnalysisState({
        filterInput: currentUrl,
        result: {
          ok: false,
          message: String(cause),
          normalized_url: "",
          parsed_app_token: "",
          parsed_table_id: "",
          parsed_view_id: null,
          filter_field_name: form.progress_field_name.trim() || "回复进度",
          filter_value: "待分析",
          content_field_name: null,
          total_count: 0,
          matched_count: 0,
          rows: []
        }
      });
    } finally {
      setIsFilteringPendingAnalysis(false);
    }
  }

  const availableFieldOptions: FeishuBitableFieldInfo[] = useMemo(() => {
    if (fieldsState?.result.ok !== true) return [];
    return fieldsState.result.fields;
  }, [fieldsState]);

  async function handleSave(): Promise<string | null> {
    // 保存时会尽量复用最近一次成功验证得到的 normalized_url，
    // 避免把用户输入里的临时格式差异直接落进数据库。
    if (form.name.trim() === "") {
      setError("请输入 Agent 名称。");
      return null;
    }
    if (!modelValidation.isRunnable) {
      setError(modelValidation.message);
      openModelSettings(form.model_config.provider);
      return null;
    }
    setError("");
    setIsSaving(true);
    try {
      const payload = {
        name: form.name,
        description: form.description,
        enabled: form.enabled,
        poll_interval_minutes: form.poll_interval_minutes,
        feishu_bitable_url:
          isValidationPassed && validationState != null
            ? validationState.result.normalized_url || form.feishu_bitable_url
            : form.feishu_bitable_url,
        model_config: form.model_config,
        knowledge_scope_type: form.knowledge_scope_type,
        knowledge_scope_id: form.knowledge_scope_type === "tree_recursive" ? form.knowledge_scope_id : null,
        question_field_name: form.question_field_name,
        answer_field_name: form.answer_field_name,
        link_field_name: form.link_field_name,
        progress_field_name: form.progress_field_name,
        status_field_name: form.status_field_name,
        module_field_name: form.module_field_name,
        registrant_field_name: form.registrant_field_name,
        feedback_result_field_name: form.feedback_result_field_name,
        feedback_final_answer_field_name: form.feedback_final_answer_field_name,
        feedback_comment_field_name: form.feedback_comment_field_name,
        confidence_field_name: form.confidence_field_name,
        hit_count_field_name: form.hit_count_field_name,
        support_owner_rules: form.support_owner_rules
          .map((item) => ({
            module_value: item.module_value.trim(),
            yht_user_id: item.yht_user_id.trim()
          }))
          .filter((item) => item.module_value !== "" && item.yht_user_id !== ""),
        fallback_support_yht_user_id: form.fallback_support_yht_user_id.trim(),
        digest_enabled: form.digest_enabled,
        digest_recipient_emails: parseCommaList(form.digest_recipient_emails_text),
        case_review_enabled: form.case_review_enabled
      };
      if (form.id == null) {
        const created = await createSupportAgent(payload);
        await refreshAgents(created.id, { resetValidation: false });
        return created.id;
      } else {
        const updated = await updateSupportAgent(form.id, payload);
        await refreshAgents(updated.id, { resetValidation: false });
        return updated.id;
      }
    } catch (cause) {
      setError(String(cause));
    } finally {
      setIsSaving(false);
    }
    return null;
  }

  async function handleRun() {
    // 正式运行前要通过三道前置条件：
    // 1. 模型可运行；2. 飞书全局凭据已配置；3. 当前 bitable 地址已验证通过。
    if (!modelValidation.isRunnable) {
      setError(modelValidation.message);
      openModelSettings(form.model_config.provider);
      return;
    }
    if (!feishuSettings?.configured) {
      setError("当前还没有保存飞书 App ID / App Secret，请先到设置 -> 飞书设置完成配置。");
      openFeishuSettings();
      return;
    }
    if (!isValidationPassed) {
      setError("请先验证飞书多维表格地址通过后再立即运行。");
      return;
    }

    let agentId = form.id ?? null;
    if (form.id == null) {
      agentId = await handleSave();
    }
    if (agentId == null) return;

    setError("");
    setIsRunning(true);
    try {
      await runSupportAgent(agentId);
      await selectAgent(agentId, { resetValidation: false });
    } catch (cause) {
      setError(String(cause));
    } finally {
      setIsRunning(false);
    }
  }

  async function handleSyncFeedback() {
    // 反馈同步会把平台侧的人工处理事实拉回本地，
    // 供洞察统计、案例候选沉淀和 digest 汇总继续复用。
    if (form.id == null) {
      setError("请先保存当前 Agent，再执行反馈同步。");
      return;
    }

    setIsSyncingFeedback(true);
    setError("");
    try {
      const result = await syncSupportAgentFeedback(form.id);
      setFeedbackSyncResult(result);
      const [nextCaseCandidates, nextDigestRuns, nextInsights] = await Promise.all([
        listSupportAgentCaseCandidates(form.id),
        listSupportAgentDigestRuns(form.id),
        getSupportAgentInsights(form.id)
      ]);
      setCaseCandidates(nextCaseCandidates);
      setDigestRuns(nextDigestRuns);
      setInsights(nextInsights);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setIsSyncingFeedback(false);
    }
  }

  async function handleRunDigest() {
    // digest 不是逐行处理问题，而是对一段时间内的运行结果做汇总和邮件摘要。
    if (form.id == null) {
      setError("请先保存当前 Agent，再执行立即汇总。");
      return;
    }

    setIsDigesting(true);
    setError("");
    try {
      await runSupportAgentDigest(form.id);
      const [nextDigestRuns, nextCaseCandidates, nextAgent] = await Promise.all([
        listSupportAgentDigestRuns(form.id),
        listSupportAgentCaseCandidates(form.id),
        getSupportAgent(form.id)
      ]);
      setDigestRuns(nextDigestRuns);
      setCaseCandidates(nextCaseCandidates);
      setForm((current) => ({
        ...current,
        last_digest_at: nextAgent.last_digest_at ?? null,
        next_digest_at: nextAgent.next_digest_at ?? null
      }));
    } catch (cause) {
      setError(String(cause));
    } finally {
      setIsDigesting(false);
    }
  }

  return (
    <div className="grid min-h-full grid-cols-1 xl:h-full xl:min-h-0 xl:overflow-hidden xl:grid-cols-[280px_minmax(0,1fr)_420px]">
      <aside className="border-b border-slate-800 bg-slate-900/50 p-5 xl:min-h-0 xl:overflow-y-auto xl:border-b-0 xl:border-r">
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="text-xs uppercase tracking-[0.35em] text-amber-300">支持问题 Agent</div>
            <h2 className="mt-2 text-xl font-semibold">Agent 列表</h2>
          </div>
          <button
            className="rounded-xl border border-slate-700 px-3 py-2 text-sm hover:border-amber-300"
            onClick={() => {
              setForm(buildEmptyForm());
              setRuns([]);
              setCaseCandidates([]);
              setDigestRuns([]);
              setInsights(null);
              setFeedbackSyncResult(null);
              resetDiagnostics();
              setError("");
            }}
          >
            新建
          </button>
        </div>

        <div className="mt-5 space-y-3">
          {isBootstrapping && (
            <div className="rounded-2xl border border-slate-800 bg-slate-900 px-4 py-4 text-sm text-slate-400">
              正在加载支持问题 Agent...
            </div>
          )}
          {agents.map((agent) => (
            <button
              key={agent.id}
              className={
                "w-full rounded-2xl border p-4 text-left transition " +
                (form.id === agent.id
                  ? "border-amber-300/60 bg-amber-300/10"
                  : "border-slate-800 bg-slate-900 hover:border-slate-700")
              }
              onClick={() => {
                void selectAgent(agent.id).catch((cause) => setError(String(cause)));
              }}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="font-medium">{agent.name}</div>
                <div className={"text-xs " + (agent.enabled ? "text-emerald-300" : "text-slate-500")}>
                  {agent.enabled ? "启用中" : "已停用"}
                </div>
              </div>
              <div className="mt-2 line-clamp-2 text-sm text-slate-400">{agent.description || "飞书多维表格 + RAG 回写"}</div>
              <div className="mt-3 text-xs text-slate-500">轮巡：{agent.poll_interval_minutes} 分钟</div>
                      <div className="mt-1 text-xs text-slate-500">
                最近：{agent.last_run_status ?? "未运行"} · {agent.last_run_at ? formatDate(agent.last_run_at) : "-"}
              </div>
            </button>
          ))}
          {agents.length === 0 && <div className="text-sm text-slate-500">还没有支持问题 Agent，可以先创建一个。</div>}
          {error !== "" && !isBootstrapping && (
            <button
              className="w-full rounded-xl border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:border-slate-500"
              onClick={() => {
                void bootstrap().catch((cause) => setError(String(cause)));
              }}
              type="button"
            >
              重新加载
            </button>
          )}
        </div>
      </aside>

      <main className="border-b border-slate-800 px-6 py-6 xl:min-h-0 xl:overflow-y-auto xl:border-b-0 xl:border-r">
        <div className="rounded-3xl border border-slate-800 bg-slate-900 p-5">
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="text-sm text-slate-400">飞书多维表格 + RAG 回写</div>
              <h3 className="mt-1 text-xl font-semibold">支持问题 Agent 配置</h3>
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2">
              <button
                className="rounded-xl border border-emerald-400/40 px-4 py-2 text-sm text-emerald-200 transition hover:bg-emerald-400/10 disabled:border-slate-700 disabled:text-slate-500"
                onClick={() => {
                  void handleLoadFields();
                }}
                disabled={isBootstrapping || isActionBusy || fieldsBlockedReason !== ""}
              >
                {isLoadingFields ? "读取中..." : "读取字段"}
              </button>
              <button
                className="rounded-xl border border-sky-400/40 px-4 py-2 text-sm text-sky-200 transition hover:bg-sky-400/10 disabled:border-slate-700 disabled:text-slate-500"
                onClick={() => {
                  void handlePreviewBitable();
                }}
                disabled={isBootstrapping || isActionBusy || previewBlockedReason !== ""}
              >
                {isPreviewing ? "拉取中..." : "拉取表格"}
              </button>
              <button
                className="rounded-xl border border-cyan-400/40 px-4 py-2 text-sm text-cyan-200 transition hover:bg-cyan-400/10 disabled:border-slate-700 disabled:text-slate-500"
                onClick={() => {
                  void handleFilterPendingAnalysis();
                }}
                disabled={isBootstrapping || isActionBusy || pendingAnalysisBlockedReason !== ""}
              >
                {isFilteringPendingAnalysis ? "筛选中..." : "筛选待分析"}
              </button>
              <button
                className="rounded-xl border border-violet-400/40 px-4 py-2 text-sm text-violet-200 transition hover:bg-violet-400/10 disabled:border-slate-700 disabled:text-slate-500"
                onClick={() => {
                  void handleWriteValidation();
                }}
                disabled={isBootstrapping || isActionBusy || writeValidationBlockedReason !== ""}
              >
                {isWriteValidating ? "验证中..." : "编辑验证"}
              </button>
              <button
                className="rounded-xl bg-amber-300 px-4 py-2 text-sm font-medium text-slate-950 hover:bg-amber-200 disabled:bg-slate-700"
                onClick={() => {
                  void handleSave();
                }}
                disabled={isBootstrapping || isSaving}
              >
                {isSaving ? "保存中..." : "保存配置"}
              </button>
            </div>
          </div>

          <div className="mt-5 grid gap-4 xl:grid-cols-2">
            <label className="grid gap-1 text-sm xl:col-span-2">
              <span className="text-slate-400">名称</span>
              <input
                className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                value={form.name}
                onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
              />
            </label>

            <label className="grid gap-1 text-sm xl:col-span-2">
              <span className="text-slate-400">描述</span>
              <input
                className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                value={form.description}
                onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))}
                placeholder="例如：部门支持问题自动回写"
              />
            </label>

            <label className="grid gap-1 text-sm">
              <span className="text-slate-400">状态</span>
              <div className="flex gap-2">
                <button
                  className={
                    "rounded-xl border px-3 py-2 text-sm transition " +
                    (form.enabled
                      ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-200"
                      : "border-slate-700 text-slate-400 hover:border-slate-600")
                  }
                  onClick={() => setForm((current) => ({ ...current, enabled: true }))}
                >
                  启用
                </button>
                <button
                  className={
                    "rounded-xl border px-3 py-2 text-sm transition " +
                    (!form.enabled
                      ? "border-rose-400/40 bg-rose-400/10 text-rose-200"
                      : "border-slate-700 text-slate-400 hover:border-slate-600")
                  }
                  onClick={() => setForm((current) => ({ ...current, enabled: false }))}
                >
                  停用
                </button>
              </div>
            </label>

            <label className="grid gap-1 text-sm">
              <span className="text-slate-400">轮巡间隔（分钟）</span>
              <input
                type="number"
                min={1}
                max={24 * 60}
                className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                value={form.poll_interval_minutes}
                onChange={(event) =>
                  setForm((current) => ({ ...current, poll_interval_minutes: Number(event.target.value || 30) }))
                }
              />
            </label>

            <label className="grid gap-1 text-sm xl:col-span-2">
              <span className="text-slate-400">飞书多维表格地址</span>
              <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_144px]">
                <input
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                  value={form.feishu_bitable_url}
                  onChange={(event) => setForm((current) => ({ ...current, feishu_bitable_url: event.target.value }))}
                  placeholder="粘贴完整飞书多维表格链接，例如 https://xxx.feishu.cn/base/..."
                />
                <button
                  className="rounded-xl border border-sky-400/40 px-3 py-2 text-sm text-sky-200 transition hover:bg-sky-400/10 disabled:border-slate-700 disabled:text-slate-500"
                  onClick={() => {
                    void handleValidateAddress();
                  }}
                  disabled={isValidating}
                >
                  {isValidating ? "验证中..." : "验证地址"}
                </button>
              </div>
            </label>

            {validationState != null && (
              <div
                className={
                  "rounded-2xl border px-4 py-3 text-sm leading-6 xl:col-span-2 " +
                  (validationState.result.ok
                    ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-200"
                    : "border-rose-400/30 bg-rose-400/10 text-rose-200")
                }
              >
                <div className="font-medium">{validationState.result.ok ? "地址可用" : "地址验证失败"}</div>
                <div className="mt-1">{validationState.result.message}</div>
                {validationState.result.normalized_url !== "" && (
                  <div className="mt-2 text-xs text-slate-300">规范地址：{validationState.result.normalized_url}</div>
                )}
                {validationState.result.parsed_app_token !== "" && (
                  <div className="mt-1 text-xs text-slate-300">Base Token：{validationState.result.parsed_app_token}</div>
                )}
                {validationState.result.parsed_table_id !== "" && (
                  <div className="mt-1 text-xs text-slate-300">Table ID：{validationState.result.parsed_table_id}</div>
                )}
                {validationState.result.parsed_view_id && (
                  <div className="mt-1 text-xs text-slate-300">View ID：{validationState.result.parsed_view_id}</div>
                )}
              </div>
            )}

            {previewState != null && (
              <div
                className={
                  "rounded-2xl border px-4 py-3 text-sm leading-6 xl:col-span-2 " +
                  (previewState.result.ok
                    ? "border-sky-400/30 bg-sky-400/10 text-sky-100"
                    : "border-rose-400/30 bg-rose-400/10 text-rose-200")
                }
              >
                <div className="font-medium">{previewState.result.ok ? "表格预览结果" : "拉取表格失败"}</div>
                <div className="mt-1">{previewState.result.message}</div>
                {previewState.result.normalized_url !== "" && (
                  <div className="mt-2 text-xs text-slate-300">规范地址：{previewState.result.normalized_url}</div>
                )}
                {previewState.result.parsed_app_token !== "" && (
                  <div className="mt-1 text-xs text-slate-300">Base Token：{previewState.result.parsed_app_token}</div>
                )}
                {previewState.result.parsed_table_id !== "" && (
                  <div className="mt-1 text-xs text-slate-300">Table ID：{previewState.result.parsed_table_id}</div>
                )}
                {previewState.result.parsed_view_id && (
                  <div className="mt-1 text-xs text-slate-300">View ID：{previewState.result.parsed_view_id}</div>
                )}
                <div className="mt-2 text-xs text-slate-300">
                  预览行数：{previewState.result.preview_count}
                  {previewState.result.has_more ? " · 仅展示前 5 行，后续还有更多" : ""}
                </div>
                <div className="mt-3 space-y-3">
                  {previewState.result.preview_rows.map((row, index) => (
                    <div key={row.record_id || String(index)} className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
                      <div className="text-xs text-slate-400">record_id：{row.record_id || "-"}</div>
                      <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all text-xs text-slate-200">
                        {JSON.stringify(row.fields, null, 2)}
                      </pre>
                    </div>
                  ))}
                  {previewState.result.preview_rows.length === 0 && (
                    <div className="rounded-xl border border-dashed border-slate-700 px-3 py-4 text-xs text-slate-400">
                      当前表还没有数据。
                    </div>
                  )}
                </div>
              </div>
            )}

            {fieldsState != null && (
              <div
                className={
                  "rounded-2xl border px-4 py-3 text-sm leading-6 xl:col-span-2 " +
                  (fieldsState.result.ok
                    ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-100"
                    : "border-rose-400/30 bg-rose-400/10 text-rose-200")
                }
              >
                <div className="font-medium">{fieldsState.result.ok ? "字段读取结果" : "字段读取失败"}</div>
                <div className="mt-1">{fieldsState.result.message}</div>
                {fieldsState.result.normalized_url !== "" && (
                  <div className="mt-2 text-xs text-slate-300">规范地址：{fieldsState.result.normalized_url}</div>
                )}
                {fieldsState.result.fields.length > 0 && (
                  <div className="mt-2 text-xs text-slate-300">
                    字段来源：{fieldsState.result.source === "metadata_api" ? "飞书字段元数据" : "表格预览推断"}
                  </div>
                )}
                <div className="mt-3 flex flex-wrap gap-2">
                  {fieldsState.result.fields.map((field) => (
                    <span
                      key={field.field_id ?? field.field_name}
                      className="rounded-full border border-slate-700 bg-slate-950/70 px-3 py-1 text-xs text-slate-200"
                    >
                      {field.field_name}
                    </span>
                  ))}
                  {fieldsState.result.fields.length === 0 && (
                    <span className="text-xs text-slate-400">当前没有读取到字段。</span>
                  )}
                </div>
              </div>
            )}

            {writeValidationState != null && (
              <div
                className={
                  "rounded-2xl border px-4 py-3 text-sm leading-6 xl:col-span-2 " +
                  (writeValidationState.result.ok
                    ? "border-violet-400/30 bg-violet-400/10 text-violet-100"
                    : "border-rose-400/30 bg-rose-400/10 text-rose-200")
                }
              >
                <div className="font-medium">{writeValidationState.result.ok ? "编辑验证结果" : "编辑验证失败"}</div>
                <div className="mt-1">{writeValidationState.result.message}</div>
                {writeValidationState.result.normalized_url !== "" && (
                  <div className="mt-2 text-xs text-slate-300">规范地址：{writeValidationState.result.normalized_url}</div>
                )}
                {writeValidationState.result.created_record_id && (
                  <div className="mt-1 text-xs text-slate-300">创建 record_id：{writeValidationState.result.created_record_id}</div>
                )}
                {writeValidationState.result.updated_record_id && (
                  <div className="mt-1 text-xs text-slate-300">更新 record_id：{writeValidationState.result.updated_record_id}</div>
                )}
                {writeValidationState.result.deleted_record_id && (
                  <div className="mt-1 text-xs text-slate-300">删除 record_id：{writeValidationState.result.deleted_record_id}</div>
                )}
                {writeValidationState.result.used_create_field_name && (
                  <div className="mt-1 text-xs text-slate-300">创建使用字段：{writeValidationState.result.used_create_field_name}</div>
                )}
                {writeValidationState.result.used_update_field_name && (
                  <div className="mt-1 text-xs text-slate-300">更新使用字段：{writeValidationState.result.used_update_field_name}</div>
                )}
                {Object.keys(writeValidationState.result.created_fields_preview).length > 0 && (
                  <div className="mt-3 rounded-xl border border-slate-800 bg-slate-950/60 p-3">
                    <div className="text-xs text-slate-400">创建字段预览</div>
                    <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all text-xs text-slate-200">
                      {JSON.stringify(writeValidationState.result.created_fields_preview, null, 2)}
                    </pre>
                  </div>
                )}
                {Object.keys(writeValidationState.result.updated_fields_preview).length > 0 && (
                  <div className="mt-3 rounded-xl border border-slate-800 bg-slate-950/60 p-3">
                    <div className="text-xs text-slate-400">更新字段预览</div>
                    <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all text-xs text-slate-200">
                      {JSON.stringify(writeValidationState.result.updated_fields_preview, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            )}

            {pendingAnalysisState != null && (
              <div
                className={
                  "rounded-2xl border px-4 py-3 text-sm leading-6 xl:col-span-2 " +
                  (pendingAnalysisState.result.ok
                    ? "border-cyan-400/30 bg-cyan-400/10 text-cyan-100"
                    : "border-rose-400/30 bg-rose-400/10 text-rose-200")
                }
              >
                <div className="font-medium">{pendingAnalysisState.result.ok ? "待分析筛选结果" : "筛选待分析失败"}</div>
                <div className="mt-1">{pendingAnalysisState.result.message}</div>
                {pendingAnalysisState.result.normalized_url !== "" && (
                  <div className="mt-2 text-xs text-slate-300">规范地址：{pendingAnalysisState.result.normalized_url}</div>
                )}
                <div className="mt-1 text-xs text-slate-300">
                  筛选条件：{pendingAnalysisState.result.filter_field_name} = {pendingAnalysisState.result.filter_value}
                </div>
                <div className="mt-1 text-xs text-slate-300">
                  总行数：{pendingAnalysisState.result.total_count} · 命中：{pendingAnalysisState.result.matched_count}
                </div>
                {pendingAnalysisState.result.content_field_name && (
                  <div className="mt-1 text-xs text-slate-300">内容列：{pendingAnalysisState.result.content_field_name}</div>
                )}
                <div className="mt-3 space-y-3">
                  {pendingAnalysisState.result.rows.map((row, index) => (
                    <div key={row.record_id || String(index)} className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
                      <div className="text-xs text-slate-400">record_id：{row.record_id || "-"}</div>
                      {row.content !== "" && <div className="mt-2 whitespace-pre-wrap break-all text-sm text-slate-100">{row.content}</div>}
                      <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all text-xs text-slate-200">
                        {JSON.stringify(row.fields, null, 2)}
                      </pre>
                    </div>
                  ))}
                  {pendingAnalysisState.result.rows.length === 0 && pendingAnalysisState.result.ok && (
                    <div className="rounded-xl border border-dashed border-slate-700 px-3 py-4 text-xs text-slate-400">
                      当前没有筛选到 `回复进度 = 待分析` 的数据。
                    </div>
                  )}
                </div>
              </div>
            )}

            <div className="xl:col-span-2">
              <ModelSelector
                value={form.model_config}
                onChange={(nextModelConfig) => setForm((current) => ({ ...current, model_config: nextModelConfig }))}
              />
            </div>

            <label className="grid gap-1 text-sm">
              <span className="text-slate-400">知识范围</span>
              <select
                className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                value={form.knowledge_scope_type}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    knowledge_scope_type: event.target.value as ScopeType,
                    knowledge_scope_id: event.target.value === "tree_recursive" ? current.knowledge_scope_id ?? "root" : null
                  }))
                }
              >
                <option value="global">global</option>
                <option value="tree_recursive">tree_recursive</option>
              </select>
            </label>

            {form.knowledge_scope_type === "tree_recursive" && (
              <label className="grid gap-1 text-sm">
                <span className="text-slate-400">树节点</span>
                <select
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                  value={form.knowledge_scope_id ?? "root"}
                  onChange={(event) => setForm((current) => ({ ...current, knowledge_scope_id: event.target.value }))}
                >
                  {treeOptions.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>

          <div className="mt-6 rounded-2xl border border-slate-800 bg-slate-950/50 p-4">
            <div className="text-sm font-medium text-slate-100">案例审核与单 Agent 汇总</div>
            <div className="mt-4 grid gap-4 xl:grid-cols-2">
              <label className="grid gap-1 text-sm">
                <span className="text-slate-400">案例审核中心</span>
                <div className="flex gap-2">
                  <button
                    className={
                      "rounded-xl border px-3 py-2 text-sm transition " +
                      (form.case_review_enabled
                        ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-200"
                        : "border-slate-700 text-slate-400 hover:border-slate-600")
                    }
                    onClick={() => setForm((current) => ({ ...current, case_review_enabled: true }))}
                  >
                    开启
                  </button>
                  <button
                    className={
                      "rounded-xl border px-3 py-2 text-sm transition " +
                      (!form.case_review_enabled
                        ? "border-rose-400/40 bg-rose-400/10 text-rose-200"
                        : "border-slate-700 text-slate-400 hover:border-slate-600")
                    }
                    onClick={() => setForm((current) => ({ ...current, case_review_enabled: false }))}
                  >
                    关闭
                  </button>
                </div>
              </label>

              <label className="grid gap-1 text-sm">
                <span className="text-slate-400">周期汇总邮件</span>
                <div className="flex gap-2">
                  <button
                    className={
                      "rounded-xl border px-3 py-2 text-sm transition " +
                      (form.digest_enabled
                        ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-200"
                        : "border-slate-700 text-slate-400 hover:border-slate-600")
                    }
                    onClick={() => setForm((current) => ({ ...current, digest_enabled: true }))}
                  >
                    开启
                  </button>
                  <button
                    className={
                      "rounded-xl border px-3 py-2 text-sm transition " +
                      (!form.digest_enabled
                        ? "border-rose-400/40 bg-rose-400/10 text-rose-200"
                        : "border-slate-700 text-slate-400 hover:border-slate-600")
                    }
                    onClick={() => setForm((current) => ({ ...current, digest_enabled: false }))}
                  >
                    关闭
                  </button>
                </div>
              </label>

              <label className="grid gap-1 text-sm xl:col-span-2">
                <span className="text-slate-400">汇总收件人（逗号或换行分隔）</span>
                <textarea
                  className="min-h-[88px] rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                  value={form.digest_recipient_emails_text}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, digest_recipient_emails_text: event.target.value }))
                  }
                  placeholder="例如：leader@example.com, owner@example.com"
                />
              </label>
            </div>

            <div className="mt-4 grid gap-1 text-xs text-slate-500">
              <div>固定汇总周期：每周一 09:00（Asia/Shanghai）。</div>
              <div>最近 digest：{form.last_digest_at ? formatDate(form.last_digest_at) : "-"}</div>
              <div>下次 digest：{form.next_digest_at ? formatDate(form.next_digest_at) : "-"}</div>
            </div>
          </div>

          <div className="mt-6 rounded-2xl border border-slate-800 bg-slate-950/50 p-4">
            <div className="text-sm font-medium text-slate-100">字段映射</div>
            <div className="mt-4 grid gap-4 xl:grid-cols-2">
              <label className="grid gap-1 text-sm">
                <span className="text-slate-400">问题列</span>
                <input
                  list="support-agent-feishu-fields"
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                  value={form.question_field_name}
                  onChange={(event) => setForm((current) => ({ ...current, question_field_name: event.target.value }))}
                />
              </label>
              <label className="grid gap-1 text-sm">
                <span className="text-slate-400">AI 解决方案列</span>
                <input
                  list="support-agent-feishu-fields"
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                  value={form.answer_field_name}
                  onChange={(event) => setForm((current) => ({ ...current, answer_field_name: event.target.value }))}
                />
              </label>
              <label className="grid gap-1 text-sm">
                <span className="text-slate-400">相关文档链接列</span>
                <input
                  list="support-agent-feishu-fields"
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                  value={form.link_field_name}
                  onChange={(event) => setForm((current) => ({ ...current, link_field_name: event.target.value }))}
                />
              </label>
              <label className="grid gap-1 text-sm">
                <span className="text-slate-400">回复进度列</span>
                <input
                  list="support-agent-feishu-fields"
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                  value={form.progress_field_name}
                  onChange={(event) => setForm((current) => ({ ...current, progress_field_name: event.target.value }))}
                />
              </label>
              <label className="grid gap-1 text-sm">
                <span className="text-slate-400">人工处理结果列</span>
                <input
                  list="support-agent-feishu-fields"
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                  value={form.feedback_result_field_name}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, feedback_result_field_name: event.target.value }))
                  }
                />
              </label>
              <label className="grid gap-1 text-sm">
                <span className="text-slate-400">人工最终方案列</span>
                <input
                  list="support-agent-feishu-fields"
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                  value={form.feedback_final_answer_field_name}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, feedback_final_answer_field_name: event.target.value }))
                  }
                />
              </label>
              <label className="grid gap-1 text-sm">
                <span className="text-slate-400">反馈备注列</span>
                <input
                  list="support-agent-feishu-fields"
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                  value={form.feedback_comment_field_name}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, feedback_comment_field_name: event.target.value }))
                  }
                />
              </label>
              <label className="grid gap-1 text-sm">
                <span className="text-slate-400">模块列</span>
                <input
                  list="support-agent-feishu-fields"
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                  value={form.module_field_name}
                  onChange={(event) => setForm((current) => ({ ...current, module_field_name: event.target.value }))}
                />
              </label>
              <label className="grid gap-1 text-sm">
                <span className="text-slate-400">登记人列</span>
                <input
                  list="support-agent-feishu-fields"
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                  value={form.registrant_field_name}
                  onChange={(event) => setForm((current) => ({ ...current, registrant_field_name: event.target.value }))}
                />
              </label>
              <label className="grid gap-1 text-sm">
                <span className="text-slate-400">AI 置信度列</span>
                <input
                  list="support-agent-feishu-fields"
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                  value={form.confidence_field_name}
                  onChange={(event) => setForm((current) => ({ ...current, confidence_field_name: event.target.value }))}
                />
              </label>
              <label className="grid gap-1 text-sm">
                <span className="text-slate-400">命中知识数列</span>
                <input
                  list="support-agent-feishu-fields"
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                  value={form.hit_count_field_name}
                  onChange={(event) => setForm((current) => ({ ...current, hit_count_field_name: event.target.value }))}
                />
              </label>
            </div>
            <datalist id="support-agent-feishu-fields">
              {availableFieldOptions.map((field) => (
                <option key={field.field_id ?? field.field_name} value={field.field_name} />
              ))}
            </datalist>
            <div className="mt-4 text-xs leading-6 text-slate-500">
              固定补充列按字段名读取：`补充1`、`补充2`、`补充3`、`补充4`、`补充5`。轮巡只处理 `回复进度列` 为 `待分析` 或 `失败待重试` 的行；低置信度或无命中结果都会写入草稿并转成 `待人工确认`。
            </div>
          </div>

          <div className="mt-6 rounded-2xl border border-slate-800 bg-slate-950/50 p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-sm font-medium text-slate-100">人工确认通知路由</div>
                <div className="mt-1 text-xs text-slate-500">
                  按“模块列”当前值匹配负责人 userId；未命中时走兜底负责人。登记人通知会从“登记人列”中提取 userId。
                </div>
              </div>
              <button
                className="rounded-xl border border-sky-400/40 px-3 py-2 text-sm text-sky-200 transition hover:bg-sky-400/10"
                onClick={() =>
                  setForm((current) => ({
                    ...current,
                    support_owner_rules: current.support_owner_rules.concat(buildEmptyOwnerRule())
                  }))
                }
              >
                新增模块负责人
              </button>
            </div>

            <div className="mt-4 grid gap-4 xl:grid-cols-2">
              <label className="grid gap-1 text-sm">
                <span className="text-slate-400">兜底负责人 userId</span>
                <input
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                  value={form.fallback_support_yht_user_id}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, fallback_support_yht_user_id: event.target.value }))
                  }
                  placeholder="未命中模块时发送给这个 userId"
                />
              </label>
            </div>

            <div className="mt-4 space-y-3">
              {form.support_owner_rules.map((rule, index) => (
                <div key={`owner-rule-${index}`} className="rounded-2xl border border-slate-800 bg-slate-900/80 p-4">
                  <div className="grid gap-3 xl:grid-cols-[1fr_1fr_auto]">
                    <label className="grid gap-1 text-sm">
                      <span className="text-slate-400">模块值</span>
                      <input
                        className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                        value={rule.module_value}
                        onChange={(event) =>
                          setForm((current) => ({
                            ...current,
                            support_owner_rules: current.support_owner_rules.map((item, itemIndex) =>
                              itemIndex === index ? { ...item, module_value: event.target.value } : item
                            )
                          }))
                        }
                        placeholder="例如：工作台"
                      />
                    </label>
                    <label className="grid gap-1 text-sm">
                      <span className="text-slate-400">负责人 userId</span>
                      <input
                        className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                        value={rule.yht_user_id}
                        onChange={(event) =>
                          setForm((current) => ({
                            ...current,
                            support_owner_rules: current.support_owner_rules.map((item, itemIndex) =>
                              itemIndex === index ? { ...item, yht_user_id: event.target.value } : item
                            )
                          }))
                        }
                        placeholder="例如：099187e8-348e-46f3-8c53-14357870d4d8"
                      />
                    </label>
                    <div className="flex items-end">
                      <button
                        className="rounded-xl border border-rose-400/40 px-3 py-2 text-sm text-rose-200 transition hover:bg-rose-400/10"
                        onClick={() =>
                          setForm((current) => ({
                            ...current,
                            support_owner_rules: current.support_owner_rules.filter((_, itemIndex) => itemIndex !== index)
                          }))
                        }
                      >
                        删除
                      </button>
                    </div>
                  </div>
                </div>
              ))}
              {form.support_owner_rules.length === 0 && (
                <div className="rounded-2xl border border-dashed border-slate-700 px-4 py-5 text-sm text-slate-500">
                  当前还没有模块负责人规则。命中 `待人工确认` 时会优先按模块值匹配负责人，未配置时只会尝试兜底负责人。
                </div>
              )}
            </div>
          </div>

          {error !== "" && <div className="mt-5 text-sm text-rose-300">{error}</div>}
        </div>

        <div className="mt-6 rounded-3xl border border-slate-800 bg-slate-900 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-sm text-slate-400">人工反馈 → 待审核案例 → 正式案例库</div>
              <h3 className="mt-1 text-xl font-semibold">案例候选池入口</h3>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                className="rounded-xl border border-cyan-400/40 px-4 py-2 text-sm text-cyan-200 transition hover:bg-cyan-400/10 disabled:border-slate-700 disabled:text-slate-500"
                onClick={() => {
                  void handleSyncFeedback();
                }}
                disabled={isBootstrapping || form.id == null || isActionBusy}
              >
                {isSyncingFeedback ? "同步中..." : "同步反馈"}
              </button>
              {form.id != null ? (
                <Link
                  href={`/support-agents/cases?agentId=${encodeURIComponent(form.id)}`}
                  className="rounded-xl border border-amber-400/40 px-4 py-2 text-sm text-amber-200 transition hover:bg-amber-400/10"
                >
                  进入案例候选池
                </Link>
              ) : (
                <span className="rounded-xl border border-slate-800 px-4 py-2 text-sm text-slate-500">
                  先保存 Agent 后查看案例
                </span>
              )}
            </div>
          </div>

          {feedbackSyncResult != null && (
            <div className="mt-4 rounded-2xl border border-cyan-400/20 bg-cyan-400/10 px-4 py-3 text-sm text-cyan-100">
              {feedbackSyncResult.summary}
            </div>
          )}

          <div className="mt-4 grid gap-3 md:grid-cols-3">
            <div className="rounded-2xl border border-slate-800 bg-slate-950/50 px-4 py-4">
              <div className="text-xs text-slate-500">候选总数</div>
              <div className="mt-2 text-2xl font-semibold text-slate-100">{caseCandidates.length}</div>
            </div>
            <div className="rounded-2xl border border-slate-800 bg-slate-950/50 px-4 py-4">
              <div className="text-xs text-slate-500">待审核</div>
              <div className="mt-2 text-2xl font-semibold text-amber-200">
                {caseCandidates.filter((item) => item.status === "pending_review").length}
              </div>
            </div>
            <div className="rounded-2xl border border-slate-800 bg-slate-950/50 px-4 py-4">
              <div className="text-xs text-slate-500">审核通过</div>
              <div className="mt-2 text-2xl font-semibold text-emerald-200">
                {caseCandidates.filter((item) => item.status === "approved").length}
              </div>
            </div>
          </div>

          <div className="mt-4 rounded-2xl border border-dashed border-slate-700 px-4 py-4 text-sm leading-6 text-slate-400">
            独立案例页会以表格方式展示全部候选，并支持按状态、分类、名称搜索；人工最终方案和反馈备注也在那边统一编辑。
          </div>
        </div>
      </main>

      <section className="px-5 py-6 xl:min-h-0 xl:overflow-y-auto">
        <div className="rounded-3xl border border-slate-800 bg-slate-900 p-5">
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="text-sm text-slate-400">运行与回写结果</div>
              <h3 className="mt-1 text-xl font-semibold">立即执行</h3>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                className="rounded-xl border border-violet-400/40 px-4 py-2 text-sm text-violet-200 transition hover:bg-violet-400/10 disabled:border-slate-700 disabled:text-slate-500"
                onClick={() => {
                  void handleRunDigest();
                }}
                disabled={isBootstrapping || form.id == null || isActionBusy}
              >
                {isDigesting ? "汇总中..." : "立即汇总"}
              </button>
              <button
                className="rounded-xl bg-sky-400 px-4 py-2 text-sm font-medium text-slate-950 hover:bg-sky-300 disabled:bg-slate-700"
                onClick={() => {
                  void handleRun();
                }}
                disabled={isBootstrapping || isRunning || runBlockedReason !== ""}
              >
                {isRunning ? "运行中..." : "立即运行"}
              </button>
            </div>
          </div>

          <div className="mt-4 rounded-2xl border border-slate-800 bg-slate-950/50 p-4 text-sm leading-6 text-slate-400">
            <div>飞书应用：{feishuSettings?.configured ? `已配置（${feishuSettings.app_id}）` : "未配置"}</div>
            <div className="mt-1">地址验证：{isValidationPassed ? "已通过" : "未通过"}</div>
            <div className="mt-1">最近下次轮巡：{form.next_run_at ? formatDate(form.next_run_at) : "-"}</div>
            <div className="mt-1">最近 digest：{form.last_digest_at ? formatDate(form.last_digest_at) : "-"}</div>
            <div className="mt-1">下次 digest：{form.next_digest_at ? formatDate(form.next_digest_at) : "-"}</div>
            {runBlockedReason !== "" && <div className="mt-2 text-amber-300">{runBlockedReason}</div>}
          </div>

          {insights != null && (
            <div className="mt-5 rounded-2xl border border-slate-800 bg-slate-950/50 p-4">
              <div className="text-sm font-medium text-slate-100">效果分析</div>
              <div className="mt-3 grid gap-2 text-xs text-slate-400">
                <div>采样运行数：{insights.sample_run_count}</div>
                <div>累计处理：{insights.total_processed_count}</div>
                <div>采纳率：{formatPercent(insights.acceptance_rate)}</div>
                <div>驳回率：{formatPercent(insights.rejection_rate)}</div>
                <div>低置信转人工率：{formatPercent(insights.low_confidence_rate)}</div>
                <div>无命中率：{formatPercent(insights.no_hit_rate)}</div>
                <div>人工改写率：{formatPercent(insights.manual_rewrite_rate)}</div>
              </div>
              {insights.top_categories.length > 0 && (
                <div className="mt-4">
                  <div className="text-xs font-medium text-slate-300">高频问题类型</div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {insights.top_categories.map((item) => (
                      <span
                        key={item.category}
                        className="rounded-full border border-slate-700 bg-slate-900 px-3 py-1 text-xs text-slate-200"
                      >
                        {item.category} · {item.count}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              <div className="mt-4">
                <div className="text-xs font-medium text-slate-300">优化建议</div>
                <div className="mt-2 space-y-2 text-xs text-slate-400">
                  {insights.optimization_suggestions.map((suggestion, index) => (
                    <div key={String(index)} className="rounded-xl border border-slate-800 bg-slate-900/60 px-3 py-2">
                      {suggestion}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          <div className="mt-5 space-y-4">
            {runs.map((run) => (
              <div key={run.id} className="rounded-2xl border border-slate-800 bg-slate-950/50 p-4">
                <div className="flex items-center justify-between gap-3">
                  <div className="font-medium text-slate-100">{run.status}</div>
                  <div className="text-xs text-slate-500">{formatDate(run.started_at)}</div>
                </div>
                <div className="mt-2 text-sm text-slate-300">{run.summary}</div>
                <div className="mt-3 grid gap-1 text-xs text-slate-500">
                  <div>读取行数：{run.fetched_row_count}</div>
                  <div>处理行数：{run.processed_row_count}</div>
                  <div>已生成：{run.generated_count}</div>
                  <div>待人工确认：{run.manual_review_count}</div>
                  <div>无命中：{run.no_hit_count}</div>
                  <div>失败：{run.failed_count}</div>
                </div>
                {run.error_message && <div className="mt-3 text-xs text-rose-300">{run.error_message}</div>}

                {run.row_results.length > 0 && (
                  <div className="mt-4 space-y-3">
                    {run.row_results.map((rowResult) => (
                      <div key={run.id + "-" + rowResult.record_id} className="rounded-2xl border border-slate-800 bg-slate-900/80 p-3">
                        <div className="flex items-center justify-between gap-3">
                          <div className="line-clamp-2 text-sm text-slate-100">{rowResult.question || "（问题为空）"}</div>
                          <div className="text-xs text-slate-500">{rowResult.status}</div>
                        </div>
                        <div className="mt-2 text-xs leading-6 text-slate-400">{rowResult.message}</div>
                        <div className="mt-2 grid gap-1 text-xs text-slate-500">
                          <div>问题分类：{rowResult.question_category || "-"}</div>
                          <div>命中知识数：{rowResult.retrieval_hit_count}</div>
                          <div>置信度：{formatPercent(rowResult.confidence_score)}</div>
                          <div>自检结果：{rowResult.judge_status || "-"} · {rowResult.judge_reason || "-"}</div>
                          <div>相似案例数：{rowResult.similar_case_count}</div>
                        </div>
                        {rowResult.feedback_snapshot != null && (
                          <div className="mt-2 rounded-xl border border-slate-800 bg-slate-950/60 p-2 text-xs text-slate-400">
                            <div>人工处理结果：{rowResult.feedback_snapshot.result || "-"}</div>
                            <div className="mt-1">人工最终方案：{rowResult.feedback_snapshot.final_solution || "-"}</div>
                            <div className="mt-1">反馈备注：{rowResult.feedback_snapshot.comment || "-"}</div>
                          </div>
                        )}
                        {rowResult.related_link && (
                          <a
                            className="mt-2 inline-block text-xs text-sky-300 hover:text-sky-200"
                            href={rowResult.related_link}
                            target="_blank"
                            rel="noreferrer"
                          >
                            打开相关链接
                          </a>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}

            {runs.length === 0 && (
              <div className="rounded-2xl border border-dashed border-slate-700 px-4 py-6 text-sm text-slate-500">
                还没有运行记录。保存配置后可先验证地址，再手动立即运行，也可以开启定时轮巡。
              </div>
            )}
          </div>

          <div className="mt-6 rounded-2xl border border-slate-800 bg-slate-950/50 p-4">
            <div className="text-sm font-medium text-slate-100">单 Agent 汇总记录</div>
            <div className="mt-4 space-y-3">
              {digestRuns.map((run) => (
                <div key={run.id} className="rounded-2xl border border-slate-800 bg-slate-900/80 p-3">
                  <div className="flex items-center justify-between gap-3">
                    <div className="font-medium text-slate-100">{run.status}</div>
                    <div className="text-xs text-slate-500">{formatDate(run.started_at)}</div>
                  </div>
                  <div className="mt-2 text-xs text-slate-400">{run.summary}</div>
                  <div className="mt-3 grid gap-1 text-xs text-slate-500">
                    <div>收件人：{run.recipient_emails.length > 0 ? run.recipient_emails.join(", ") : "-"}</div>
                    <div>邮件发送：{run.email_sent ? "成功" : "未发送/失败"}</div>
                    <div>AI分析完成：{run.generated_count}</div>
                    <div>待人工确认：{run.manual_review_count}</div>
                    <div>无命中：{run.no_hit_count}</div>
                    <div>失败：{run.failed_count}</div>
                    <div>直接采纳：{run.acceptance_count}</div>
                    <div>修改后采纳：{run.revised_acceptance_count}</div>
                    <div>驳回：{run.rejected_count}</div>
                    <div>新增候选案例：{run.new_candidate_count}</div>
                    <div>审核通过入库：{run.approved_candidate_count}</div>
                  </div>
                  {run.top_categories.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {run.top_categories.map((item) => (
                        <span
                          key={run.id + "-" + item.category}
                          className="rounded-full border border-slate-700 bg-slate-950/60 px-2 py-1 text-[11px] text-slate-300"
                        >
                          {item.category} · {item.count}
                        </span>
                      ))}
                    </div>
                  )}
                  {run.error_message && <div className="mt-3 text-xs text-rose-300">{run.error_message}</div>}
                </div>
              ))}
              {digestRuns.length === 0 && (
                <div className="rounded-2xl border border-dashed border-slate-700 px-4 py-5 text-sm text-slate-500">
                  当前还没有 digest 记录。保存收件人后，可以先手动执行一次“立即汇总”。
                </div>
              )}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

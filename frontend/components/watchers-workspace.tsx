"use client";

/**
 * 巡检 Agent 工作区。
 *
 * 它包含抓取配置、Cookie / Curl 解析、责任人规则、运行记录等复杂交互，
 * 是理解自动化巡检 Agent 前端配置面的核心入口。
 */
import { useEffect, useMemo, useState } from "react";

import {
  createWatcher,
  getWatcher,
  listWatcherRuns,
  listWatchers,
  runWatcher,
  testWatcherFetch,
  updateWatcher
} from "../lib/api";
import type {
  ModelConfig,
  OwnerRule,
  WatcherAgentConfig,
  WatcherAssignmentResult,
  WatcherFetchTestResponse,
  WatcherMatchMode,
  WatcherRequestMethod,
  WatcherRun
} from "../lib/types";
import { ModelSelector } from "./model-selector";
import { useModelSettings } from "./model-settings-provider";

/** 巡检 Agent 默认使用的学习模式模型配置。 */
const DEFAULT_MODEL: ModelConfig = {
  mode: "learning",
  provider: "mock",
  model: "learning-mode",
  temperature: 0.2,
  max_tokens: 1024
};

type OwnerRuleFormState = {
  assignee_code: string;
  services_text: string;
  modules_text: string;
  keywords_text: string;
  customer_issue_types_text: string;
};

type WatcherFormState = {
  id?: string;
  name: string;
  description: string;
  dashboard_url: string;
  request_method: WatcherRequestMethod;
  request_cookie: string;
  request_extra_headers_text: string;
  request_body_text: string;
  detail_url_template: string;
  detail_request_method: WatcherRequestMethod;
  detail_request_cookie: string;
  detail_request_extra_headers_text: string;
  detail_request_body_text: string;
  match_mode: WatcherMatchMode;
  poll_interval_minutes: number;
  recipient_emails_text: string;
  model_config: ModelConfig;
  enabled: boolean;
  owner_rules: OwnerRuleFormState[];
};

/** 把巡检运行时间戳格式化成中文日期时间。 */
function formatDate(value?: string | null) {
  if (value == null || value === "") return "-";
  return new Date(value).toLocaleString("zh-CN");
}

/** 把逗号、换行等分隔文本解析成字符串数组。 */
function parseCommaList(value: string): string[] {
  return value
    .split(/[\n,，;、]/)
    .map((item) => item.trim())
    .filter((item) => item !== "");
}

/** 把对象安全格式化成 JSON 文本，便于展示和调试。 */
function stringifyJson(value: unknown) {
  return JSON.stringify(value, null, 2);
}

function parseJsonObjectInput(value: string, label: string): Record<string, unknown> {
  const normalized = value.trim();
  if (normalized === "") return {};
  try {
    const parsed = JSON.parse(normalized) as unknown;
    if (parsed == null || Array.isArray(parsed) || typeof parsed !== "object") {
      throw new Error(`${label} 必须是 JSON 对象。`);
    }
    return parsed as Record<string, unknown>;
  } catch (cause) {
    if (cause instanceof Error && cause.message.includes("必须是 JSON 对象")) {
      throw cause;
    }
    throw new Error(`${label} 不是合法 JSON：${cause instanceof Error ? cause.message : String(cause)}`);
  }
}

/** 截断展示 Cookie，避免界面直接暴露完整敏感信息。 */
function previewCookie(cookie: string) {
  const normalized = cookie.trim();
  if (normalized === "") return "未配置";
  if (normalized.length <= 96) return normalized;
  return normalized.slice(0, 96) + "...";
}

/** 从 headers 中读出 Cookie，便于单独放进表单编辑。 */
function readCookieHeader(headers: Record<string, string>): string {
  return headers.Cookie ?? headers.cookie ?? "";
}

/** 从 headers 中剥离 Cookie，避免和专门的 Cookie 输入框重复。 */
function stripCookieHeader(headers: Record<string, string>): Record<string, string> {
  return Object.fromEntries(
    Object.entries(headers).filter(([key]) => key.toLowerCase() !== "cookie")
  );
}

/** 按不区分大小写的方式读取某个请求头。 */
function readHeaderValue(headers: Record<string, string>, name: string): string {
  const normalizedName = name.trim().toLowerCase();
  for (const [key, value] of Object.entries(headers)) {
    if (key.trim().toLowerCase() === normalizedName) {
      return String(value ?? "").trim();
    }
  }
  return "";
}

function looksLikeFormUrlEncodedBody(bodyText: string): boolean {
  const normalized = bodyText.trim();
  if (normalized === "" || normalized.startsWith("{") || normalized.startsWith("[")) return false;
  return normalized.includes("=") && (normalized.includes("&") || normalized.includes("%") || normalized.includes("+"));
}

/** 判断当前请求体应按 JSON 还是原始文本发送。 */
function resolveRequestBodyMode(headers: Record<string, string>, bodyText: string): "json" | "raw" {
  const contentType = readHeaderValue(headers, "content-type").toLowerCase();
  if (contentType.includes("application/x-www-form-urlencoded")) return "raw";
  if (contentType.includes("multipart/form-data")) return "raw";
  if (contentType.includes("text/plain")) return "raw";
  if (contentType.includes("application/json") || contentType.includes("+json")) return "json";

  const normalizedBody = bodyText.trim();
  if (normalizedBody === "") return "raw";
  if (looksLikeFormUrlEncodedBody(normalizedBody)) return "raw";
  try {
    parseJsonObjectInput(normalizedBody, "请求体 JSON");
    return "json";
  } catch {
    return "raw";
  }
}

/** 把 curl 里的 $'...' ANSI-C 引号解码成普通引号，兼容 Jira 复制出来的 URL。 */
function decodeAnsiCStringQuotedSegments(command: string): string {
  let result = "";
  let index = 0;

  while (index < command.length) {
    if (command[index] === "$" && command[index + 1] === "'") {
      index += 2;
      let segment = "";
      while (index < command.length) {
        const char = command[index];
        if (char === "'") {
          index += 1;
          break;
        }
        if (char === "\\" && index + 1 < command.length) {
          const next = command[index + 1];
          if (next === "u" && /^[0-9a-fA-F]{4}$/.test(command.slice(index + 2, index + 6))) {
            segment += String.fromCharCode(Number.parseInt(command.slice(index + 2, index + 6), 16));
            index += 6;
            continue;
          }
          if (next === "x" && /^[0-9a-fA-F]{2}$/.test(command.slice(index + 2, index + 4))) {
            segment += String.fromCharCode(Number.parseInt(command.slice(index + 2, index + 4), 16));
            index += 4;
            continue;
          }
          if (next === "n") {
            segment += "\n";
            index += 2;
            continue;
          }
          if (next === "t") {
            segment += "\t";
            index += 2;
            continue;
          }
          if (next === "r") {
            segment += "\r";
            index += 2;
            continue;
          }
          segment += next;
          index += 2;
          continue;
        }
        segment += char;
        index += 1;
      }
      const escaped = segment.replaceAll("\\", "\\\\").replaceAll('"', '\\"');
      result += `"${escaped}"`;
      continue;
    }
    result += command[index];
    index += 1;
  }

  return result;
}

/** 统一规范 HTTP 方法大小写。 */
function normalizeMethod(value?: string | null): WatcherRequestMethod {
  return value?.toUpperCase() === "POST" ? "POST" : "GET";
}

/** 对 curl 命令做轻量切词，为后续解析表单字段做准备。 */
function shellTokenize(command: string): string[] {
  const tokens: string[] = [];
  const normalized = decodeAnsiCStringQuotedSegments(command).replace(/\\\r?\n/g, " ");
  let current = "";
  let quote: "'" | '"' | null = null;
  let escaping = false;

  for (const char of normalized) {
    if (escaping) {
      current += char;
      escaping = false;
      continue;
    }

    if (char === "\\") {
      escaping = true;
      continue;
    }

    if (quote === "'") {
      if (char === "'") {
        quote = null;
      } else {
        current += char;
      }
      continue;
    }

    if (quote === '"') {
      if (char === '"') {
        quote = null;
      } else {
        current += char;
      }
      continue;
    }

    if (char === "'" || char === '"') {
      quote = char;
      continue;
    }

    if (/\s/.test(char)) {
      if (current !== "") {
        tokens.push(current);
        current = "";
      }
      continue;
    }

    current += char;
  }

  if (current !== "") {
    tokens.push(current);
  }

  return tokens;
}

/** 把 curl 命令解析成巡检表单可编辑的抓取配置。 */
function parseCurlCommand(command: string): {
  dashboard_url: string;
  request_method: WatcherRequestMethod;
  request_cookie: string;
  request_extra_headers_text: string;
  request_body_text: string;
} {
  const tokens = shellTokenize(command);
  if (tokens.length === 0 || tokens[0] !== "curl") {
    throw new Error("请粘贴完整的 curl 命令。");
  }

  let dashboardUrl = "";
  let requestMethod: WatcherRequestMethod = "GET";
  let requestCookie = "";
  let requestBodyText = "";
  let sawData = false;
  const requestHeaders: Record<string, string> = {};

  for (let index = 1; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token === "-X" || token === "--request") {
      requestMethod = normalizeMethod(tokens[index + 1]);
      index += 1;
      continue;
    }
    if (token === "-H" || token === "--header") {
      const rawHeader = tokens[index + 1] ?? "";
      const splitIndex = rawHeader.indexOf(":");
      if (splitIndex > 0) {
        const name = rawHeader.slice(0, splitIndex).trim();
        const value = rawHeader.slice(splitIndex + 1).trim();
        if (name.toLowerCase() === "cookie") {
          requestCookie = value;
        } else if (name !== "") {
          requestHeaders[name] = value;
        }
      }
      index += 1;
      continue;
    }
    if (token === "-b" || token === "--cookie") {
      requestCookie = tokens[index + 1] ?? "";
      index += 1;
      continue;
    }
    if (token === "-d" || token === "--data" || token === "--data-raw" || token === "--data-binary") {
      requestBodyText = tokens[index + 1] ?? "";
      sawData = true;
      if (requestMethod === "GET") {
        requestMethod = "POST";
      }
      index += 1;
      continue;
    }
    if (!token.startsWith("-") && dashboardUrl === "") {
      dashboardUrl = token;
    }
  }

  if (dashboardUrl.trim() === "") {
    throw new Error("没有从 curl 里解析到 URL。");
  }

  return {
    dashboard_url: dashboardUrl.trim(),
    request_method: requestMethod,
    request_cookie: requestCookie.trim(),
    request_extra_headers_text:
      Object.keys(requestHeaders).length > 0 ? stringifyJson(requestHeaders) : "",
    request_body_text: requestBodyText.trim()
  };
}

function emptyRule(): OwnerRuleFormState {
  return {
    assignee_code: "",
    services_text: "",
    modules_text: "",
    keywords_text: "",
    customer_issue_types_text: "",
  };
}

function normalizeDetailTemplateText(value: string): string {
  return value
    .replace(/\b[A-Z][A-Z0-9]+-\d+\b/g, "{{bug_id}}")
    .replace(/([?&]_)=\d{8,}/g, "$1={{timestamp_ms}}");
}

function buildEmptyForm(): WatcherFormState {
  return {
    name: "新的巡检 Agent",
    description: "",
    dashboard_url: "",
    request_method: "GET",
    request_cookie: "",
    request_extra_headers_text: "",
    request_body_text: "",
    detail_url_template: "",
    detail_request_method: "GET",
    detail_request_cookie: "",
    detail_request_extra_headers_text: "",
    detail_request_body_text: "",
    match_mode: "llm_fallback",
    poll_interval_minutes: 30,
    recipient_emails_text: "",
    model_config: DEFAULT_MODEL,
    enabled: true,
    owner_rules: [emptyRule()]
  };
}

function ownerRuleToForm(rule: OwnerRule): OwnerRuleFormState {
  return {
    assignee_code: rule.assignee_code ?? "",
    services_text: rule.services.join(", "),
    modules_text: rule.modules.join(", "),
    keywords_text: rule.keywords.join(", "),
    customer_issue_types_text: rule.customer_issue_types.join("、"),
  };
}

function watcherToForm(watcher: WatcherAgentConfig): WatcherFormState {
  return {
    id: watcher.id,
    name: watcher.name,
    description: watcher.description,
    dashboard_url: watcher.dashboard_url,
    request_method: watcher.request_method,
    request_cookie: readCookieHeader(watcher.request_headers),
    request_extra_headers_text:
      Object.keys(stripCookieHeader(watcher.request_headers)).length > 0
        ? stringifyJson(stripCookieHeader(watcher.request_headers))
        : "",
    request_body_text:
      watcher.request_body_text != null && watcher.request_body_text.trim() !== ""
        ? watcher.request_body_text
        : watcher.request_body_json
          ? stringifyJson(watcher.request_body_json)
          : "",
    detail_url_template: watcher.detail_url_template ?? "",
    detail_request_method: watcher.detail_request_method ?? "GET",
    detail_request_cookie: readCookieHeader(watcher.detail_request_headers),
    detail_request_extra_headers_text:
      Object.keys(stripCookieHeader(watcher.detail_request_headers)).length > 0
        ? stringifyJson(stripCookieHeader(watcher.detail_request_headers))
        : "",
    detail_request_body_text: watcher.detail_request_body_text ?? "",
    match_mode: watcher.match_mode ?? "llm_fallback",
    poll_interval_minutes: watcher.poll_interval_minutes,
    recipient_emails_text: watcher.recipient_emails.join(", "),
    model_config: watcher.model_config,
    enabled: watcher.enabled,
    owner_rules: watcher.owner_rules.length > 0 ? watcher.owner_rules.map(ownerRuleToForm) : [emptyRule()]
  };
}

function normalizeOwnerRule(rule: OwnerRuleFormState): OwnerRule | null {
  const assigneeCode = rule.assignee_code.trim();
  const services = parseCommaList(rule.services_text);
  const modules = parseCommaList(rule.modules_text);
  const keywords = parseCommaList(rule.keywords_text);
  const customerIssueTypes = parseCommaList(rule.customer_issue_types_text);

  if (
    assigneeCode === "" &&
    services.length === 0 &&
    modules.length === 0 &&
    keywords.length === 0 &&
    customerIssueTypes.length === 0
  ) {
    return null;
  }
  if (assigneeCode === "") {
    throw new Error("负责人规则里的转派目标不能为空。");
  }
  return {
    assignee_code: assigneeCode,
    services,
    modules,
    keywords,
    customer_issue_types: customerIssueTypes,
  };
}

function statusTone(status?: string | null) {
  switch (status) {
    case "success":
      return "text-emerald-300";
    case "no_change":
    case "baseline_seeded":
      return "text-sky-300";
    case "partial_success":
      return "text-amber-300";
    case "failed":
      return "text-rose-300";
    default:
      return "text-slate-400";
  }
}

function watcherEnabledLabel(watcher: WatcherAgentConfig) {
  if (watcher.enabled) return "已启用";
  if (watcher.auto_disabled_at) return "已停用（连续失败自动关闭）";
  return "已停用";
}

function watcherEnabledTone(watcher: WatcherAgentConfig) {
  if (watcher.enabled) return "text-emerald-300";
  if (watcher.auto_disabled_at) return "text-rose-300";
  return "text-slate-500";
}

export function WatchersWorkspace() {
  const { mailSettings, openMailSettings, validateModelConfig, openModelSettings } = useModelSettings();
  const [watchers, setWatchers] = useState<WatcherAgentConfig[]>([]);
  const [runs, setRuns] = useState<WatcherRun[]>([]);
  const [form, setForm] = useState<WatcherFormState>(buildEmptyForm());
  const [curlDraft, setCurlDraft] = useState("");
  const [detailCurlDraft, setDetailCurlDraft] = useState("");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [runningMode, setRunningMode] = useState<"snapshot" | "assign_current" | null>(null);
  const [isTesting, setIsTesting] = useState(false);
  const [togglingWatcherId, setTogglingWatcherId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<WatcherFetchTestResponse | null>(null);
  const [isBootstrapping, setIsBootstrapping] = useState(true);
  const modelValidation = validateModelConfig(form.model_config);
  const requiresRunnableModel = form.match_mode !== "fixed_match";

  const selectedRun = useMemo(
    () => runs.find((item) => item.id === selectedRunId) ?? runs[0] ?? null,
    [runs, selectedRunId]
  );
  const selectedWatcher = useMemo(
    () => watchers.find((item) => item.id === form.id) ?? null,
    [watchers, form.id]
  );

  async function loadWatcherDetail(watcherId: string) {
    // 左侧切换 Watcher 时，需要同步刷新两类信息：
    // 当前表单配置，以及这台 Watcher 的运行历史。
    const [watcher, watcherRuns] = await Promise.all([getWatcher(watcherId), listWatcherRuns(watcherId)]);
    setForm(watcherToForm(watcher));
    setRuns(watcherRuns);
    setSelectedRunId(watcherRuns[0]?.id ?? null);
    setTestResult(null);
  }

  async function bootstrap() {
    // 首次进入页面时，优先加载 Watcher 列表；
    // 如果已有配置，就默认打开第一条，保证页面不是空壳。
    setIsBootstrapping(true);
    setError("");
    try {
      const watcherList = await listWatchers();
      setWatchers(watcherList);
      if (watcherList.length > 0) {
        await loadWatcherDetail(watcherList[0].id);
        return;
      }
      setForm(buildEmptyForm());
      setRuns([]);
      setSelectedRunId(null);
      setTestResult(null);
    } finally {
      setIsBootstrapping(false);
    }
  }

  useEffect(() => {
    void bootstrap().catch((cause) => setError(String(cause)));
  }, []);

  async function refreshWatchers(selectId?: string) {
    // 保存、运行、启停后统一走这个刷新入口，
    // 避免每个操作都各自维护一套“刷新列表 + 切换详情”的逻辑。
    const watcherList = await listWatchers();
    setWatchers(watcherList);
    const targetId = selectId ?? watcherList[0]?.id;
    if (targetId != null) {
      await loadWatcherDetail(targetId);
      return;
    }
    setRuns([]);
    setSelectedRunId(null);
    setTestResult(null);
  }

  function buildRequestHeaders(headersText: string, cookieText: string, label: string): Record<string, string> {
    const requestHeaders = Object.fromEntries(
      Object.entries(parseJsonObjectInput(headersText, label)).map(([key, value]) => [
        key,
        String(value)
      ])
    );
    if (cookieText.trim() !== "") {
      requestHeaders.Cookie = cookieText.trim();
    }
    return requestHeaders;
  }

  function buildMainFetchRequest() {
    // 巡检抓取配置最终要还原成一个 HTTP 请求：
    // URL、方法、Headers、Body 都从表单草稿拼出来。
    const requestHeaders = buildRequestHeaders(form.request_extra_headers_text, form.request_cookie, "附加请求头 JSON");
    const normalizedRequestBodyText = form.request_body_text.trim();
    const requestBodyMode =
      form.request_method === "POST"
        ? resolveRequestBodyMode(requestHeaders, normalizedRequestBodyText)
        : "raw";
    let requestBodyJson: Record<string, unknown> | null = null;
    if (form.request_method === "POST" && normalizedRequestBodyText !== "" && requestBodyMode === "json") {
      try {
        requestBodyJson = parseJsonObjectInput(normalizedRequestBodyText, "请求体 JSON");
      } catch (cause) {
        if (!looksLikeFormUrlEncodedBody(normalizedRequestBodyText)) {
          throw cause;
        }
      }
    }
    return {
      dashboard_url: form.dashboard_url.trim(),
      request_method: form.request_method,
      request_headers: requestHeaders,
      request_body_json: requestBodyJson,
      request_body_text:
        form.request_method === "POST" && normalizedRequestBodyText !== ""
          ? normalizedRequestBodyText
          : null
    };
  }

  function buildDetailRequest() {
    const detailUrlTemplate = form.detail_url_template.trim();
    if (detailUrlTemplate === "") {
      return {
        detail_url_template: null,
        detail_request_method: "GET" as WatcherRequestMethod,
        detail_request_headers: {},
        detail_request_body_text: null
      };
    }
    const detailRequestHeaders = buildRequestHeaders(
      form.detail_request_extra_headers_text,
      form.detail_request_cookie,
      "详情附加请求头 JSON"
    );
    return {
      detail_url_template: detailUrlTemplate,
      detail_request_method: form.detail_request_method,
      detail_request_headers: detailRequestHeaders,
      detail_request_body_text:
        form.detail_request_method === "POST" && form.detail_request_body_text.trim() !== ""
          ? form.detail_request_body_text.trim()
          : null
    };
  }

  function handleParseCurl() {
    try {
      const parsed = parseCurlCommand(curlDraft);
      setForm((current) => ({
        ...current,
        dashboard_url: parsed.dashboard_url,
        request_method: parsed.request_method,
        request_cookie: parsed.request_cookie,
        request_extra_headers_text: parsed.request_extra_headers_text,
        request_body_text: parsed.request_body_text
      }));
      setError("");
      setTestResult(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  function handleParseDetailCurl() {
    try {
      const parsed = parseCurlCommand(detailCurlDraft);
      setForm((current) => ({
        ...current,
        detail_url_template: normalizeDetailTemplateText(parsed.dashboard_url),
        detail_request_method: parsed.request_method,
        detail_request_cookie: normalizeDetailTemplateText(parsed.request_cookie),
        detail_request_extra_headers_text:
          parsed.request_extra_headers_text.trim() !== ""
            ? stringifyJson(
                Object.fromEntries(
                  Object.entries(parseJsonObjectInput(parsed.request_extra_headers_text, "详情附加请求头 JSON")).map(
                    ([key, value]) => [key, normalizeDetailTemplateText(String(value))]
                  )
                )
              )
            : "",
        detail_request_body_text: normalizeDetailTemplateText(parsed.request_body_text)
      }));
      setError("");
      setTestResult(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  function buildPayload() {
    // 保存到后端前，把前端表单状态转换成后端 schema 需要的结构。
    return {
      name: form.name.trim(),
      description: form.description.trim(),
      ...buildMainFetchRequest(),
      ...buildDetailRequest(),
      match_mode: form.match_mode,
      poll_interval_minutes: Number(form.poll_interval_minutes),
      sender_email: mailSettings?.sender_email ?? "",
      recipient_emails: parseCommaList(form.recipient_emails_text),
      model_config: form.model_config,
      enabled: form.enabled,
      owner_rules: form.owner_rules.map(normalizeOwnerRule).filter((item): item is OwnerRule => item != null)
    };
  }

  async function handleSave(): Promise<string | null> {
    if (form.name.trim() === "" || form.dashboard_url.trim() === "") {
      setError("名称和面板 URL 不能为空。");
      return null;
    }
    if (requiresRunnableModel && !modelValidation.isRunnable) {
      setError(modelValidation.message);
      openModelSettings(form.model_config.provider);
      return null;
    }

    setError("");
    setIsSaving(true);
    try {
      const payload = buildPayload();
      if (form.id == null) {
        const created = await createWatcher(payload);
        await refreshWatchers(created.id);
        return created.id;
      } else {
        const updated = await updateWatcher(form.id, payload);
        await refreshWatchers(updated.id);
        return updated.id;
      }
    } catch (cause) {
      setError(String(cause));
      return null;
    } finally {
      setIsSaving(false);
    }
  }

  async function handleRun(mode: "snapshot" | "assign_current" = "snapshot") {
    // 运行前先强制保存，是为了保证：
    // “你刚刚在界面上改的配置”和“后端真正执行的配置”始终是一致的。
    if (requiresRunnableModel && !modelValidation.isRunnable) {
      setError(modelValidation.message);
      openModelSettings(form.model_config.provider);
      return;
    }

    const watcherId = await handleSave();
    if (watcherId == null) return;

    setError("");
    setRunningMode(mode);
    try {
      const run = await runWatcher(watcherId, { assign_current_list: mode === "assign_current" });
      const latestRuns = await listWatcherRuns(watcherId);
      setRuns(latestRuns);
      setSelectedRunId(run.id);
      const watcherList = await listWatchers();
      setWatchers(watcherList);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setRunningMode(null);
    }
  }

  async function handleTestFetch() {
    // “测试抓取”只验证是否能成功拉到原始面板数据，
    // 不会真正触发分配接口，也不会发送邮件。
    if (form.dashboard_url.trim() === "") {
      setError("请先填写面板 URL。");
      return;
    }

    setError("");
    setIsTesting(true);
    try {
      const result = await testWatcherFetch({
        ...buildMainFetchRequest(),
        ...buildDetailRequest(),
      });
      setTestResult(result);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setIsTesting(false);
    }
  }

  async function handleToggleWatcherEnabled(watcher: WatcherAgentConfig, nextEnabled: boolean) {
    // 列表里的启停按钮走的是轻量更新：
    // 只改 enabled 字段，不打断当前页面其余编辑状态。
    setError("");
    setTogglingWatcherId(watcher.id);
    try {
      const updated = await updateWatcher(watcher.id, { enabled: nextEnabled });
      setWatchers((current) =>
        current.map((item) => {
          if (item.id !== updated.id) return item;
          return updated;
        })
      );
      if (form.id === updated.id) {
        setForm((current) => ({ ...current, enabled: updated.enabled }));
      }
    } catch (cause) {
      setError(String(cause));
    } finally {
      setTogglingWatcherId(null);
    }
  }

  return (
    <div className="grid min-h-full grid-cols-1 xl:h-full xl:min-h-0 xl:overflow-hidden xl:grid-cols-[320px_minmax(0,1fr)_420px]">
      <aside className="border-b border-slate-800 bg-slate-900/50 p-5 xl:flex xl:min-h-0 xl:flex-col xl:overflow-hidden xl:border-b-0 xl:border-r">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-xs uppercase tracking-[0.35em] text-amber-300">巡检 Agent</div>
            <h2 className="mt-2 text-xl font-semibold">Agent 列表</h2>
          </div>
          <button
            className="rounded-xl border border-slate-700 px-3 py-2 text-sm hover:border-amber-300"
            onClick={() => {
              setForm(buildEmptyForm());
              setRuns([]);
              setSelectedRunId(null);
              setTestResult(null);
              setCurlDraft("");
              setDetailCurlDraft("");
              setError("");
            }}
          >
            新建
          </button>
        </div>

        <div className="mt-5 xl:min-h-0 xl:flex-1 xl:overflow-y-auto xl:pr-1">
          <div className="space-y-3">
            {isBootstrapping && (
              <div className="rounded-2xl border border-slate-800 bg-slate-900 px-4 py-4 text-sm text-slate-400">
                正在加载巡检 Agent...
              </div>
            )}
            {watchers.map((watcher) => (
              <div
                key={watcher.id}
                role="button"
                tabIndex={0}
                className={
                  "w-full rounded-2xl border p-4 text-left transition " +
                  (form.id === watcher.id
                    ? "border-amber-300/60 bg-amber-300/10"
                    : "border-slate-800 bg-slate-900 hover:border-slate-700")
                }
                onClick={() => {
                  void loadWatcherDetail(watcher.id).catch((cause) => setError(String(cause)));
                }}
                onKeyDown={(event) => {
                  if (event.key !== "Enter" && event.key !== " ") return;
                  event.preventDefault();
                  void loadWatcherDetail(watcher.id).catch((cause) => setError(String(cause)));
                }}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="font-medium">{watcher.name}</div>
                    <div className={"mt-1 text-xs " + watcherEnabledTone(watcher)}>
                      {watcherEnabledLabel(watcher)}
                    </div>
                  </div>
                  <button
                    className={
                      "shrink-0 rounded-full border px-3 py-1 text-xs transition " +
                      (watcher.enabled
                        ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-200 hover:border-emerald-400"
                        : "border-slate-700 bg-slate-950 text-slate-300 hover:border-slate-500")
                    }
                    disabled={togglingWatcherId === watcher.id}
                    onClick={(event) => {
                      event.stopPropagation();
                      void handleToggleWatcherEnabled(watcher, !watcher.enabled);
                    }}
                    type="button"
                  >
                    {togglingWatcherId === watcher.id ? "切换中..." : watcher.enabled ? "停用" : "启用"}
                  </button>
                </div>
                <div className="mt-2 line-clamp-2 text-sm text-slate-400">{watcher.description || watcher.dashboard_url}</div>
                <div className="mt-3 grid gap-1 text-xs text-slate-500">
                  <div>轮巡：{watcher.poll_interval_minutes} 分钟</div>
                  <div>最近运行：{formatDate(watcher.last_run_at)}</div>
                  <div className={statusTone(watcher.last_run_status)}>最近状态：{watcher.last_run_status ?? "-"}</div>
                  <div>最近新增：{watcher.last_new_bug_count}</div>
                  <div>连续失败：{watcher.consecutive_failure_count}</div>
                  {watcher.auto_disabled_at && <div className="text-rose-300">自动停用：{formatDate(watcher.auto_disabled_at)}</div>}
                </div>
              </div>
            ))}
            {watchers.length === 0 && <div className="text-sm text-slate-500">还没有巡检 Agent，可以先新建一个。</div>}
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
        </div>
      </aside>

      <main className="min-w-0 border-b border-slate-800 px-6 py-6 xl:min-h-0 xl:overflow-y-auto xl:border-b-0 xl:border-r">
        <div className="rounded-3xl border border-slate-800 bg-slate-900 p-5">
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="text-sm text-slate-400">LangGraph 巡检自动化</div>
              <h3 className="mt-1 text-xl font-semibold">巡检 Agent 配置</h3>
            </div>
            <div className="flex gap-3">
              <button
                className="rounded-xl border border-slate-700 px-4 py-2 text-sm hover:border-slate-500 disabled:opacity-50"
                onClick={() => {
                  void handleRun("snapshot");
                }}
                disabled={isBootstrapping || runningMode != null}
              >
                {runningMode === "snapshot" ? "运行中..." : "立即运行"}
              </button>
              <button
                className="rounded-xl border border-amber-500/50 px-4 py-2 text-sm text-amber-200 hover:border-amber-400 disabled:opacity-50"
                onClick={() => {
                  void handleRun("assign_current");
                }}
                disabled={isBootstrapping || runningMode != null}
              >
                {runningMode === "assign_current" ? "分配中..." : "立即运行并分配当前列表"}
              </button>
              <button
                className="rounded-xl border border-sky-500/50 px-4 py-2 text-sm text-sky-200 hover:border-sky-400 disabled:opacity-50"
                onClick={() => {
                  void handleTestFetch();
                }}
                disabled={isBootstrapping || isTesting}
              >
                {isTesting ? "检查中..." : "接口检查"}
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

          <div className="mt-5 grid gap-4 lg:grid-cols-2">
            <div className="rounded-2xl border border-slate-800 bg-slate-950/40 p-4 lg:col-span-2">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-medium text-slate-200">粘贴 curl 自动解析</div>
                  <div className="mt-1 text-xs text-slate-500">
                    支持自动回填 URL、方法、Cookie、附加请求头、请求体。
                  </div>
                </div>
                <button
                  className="rounded-xl border border-sky-500/50 px-3 py-2 text-sm text-sky-200 hover:border-sky-400"
                  onClick={handleParseCurl}
                  type="button"
                >
                  解析 curl
                </button>
              </div>
              <textarea
                className="mt-4 min-h-36 rounded-2xl border border-slate-700 bg-slate-950 px-3 py-3 font-mono text-xs leading-6"
                value={curlDraft}
                onChange={(event) => setCurlDraft(event.target.value)}
                placeholder={"curl 'https://example.com/api' \\\n  -H 'origin: https://pm.example.com' \\\n  -b 'tenant=xxx' \\\n  --data-raw '{\"pageNumber\":1}'"}
              />
            </div>
            <div className="rounded-2xl border border-slate-800 bg-slate-950/40 p-4 lg:col-span-2">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-medium text-slate-200">详情 curl 自动解析（可选）</div>
                  <div className="mt-1 text-xs text-slate-500">
                    列表接口解析出每条 `bug_id / issueKey` 后，会逐条替换详情模板里的占位符去抓工单详情。
                  </div>
                </div>
                <button
                  className="rounded-xl border border-sky-500/50 px-3 py-2 text-sm text-sky-200 hover:border-sky-400"
                  onClick={handleParseDetailCurl}
                  type="button"
                >
                  解析详情 curl
                </button>
              </div>
              <textarea
                className="mt-4 min-h-32 rounded-2xl border border-slate-700 bg-slate-950 px-3 py-3 font-mono text-xs leading-6"
                value={detailCurlDraft}
                onChange={(event) => setDetailCurlDraft(event.target.value)}
                placeholder={
                  "curl $'https://gfjira.yyrd.com/secure/AjaxIssueAction\\u0021default.jspa?issueKey=YYZJ-138373&decorator=none&_=1776688309654' \\\n  -b 'tenant_info=...' \\\n  -H 'x-requested-with: XMLHttpRequest'"
                }
              />
            </div>
            <label className="grid gap-1 text-sm">
              <span className="text-slate-400">名称</span>
              <input
                className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                value={form.name}
                onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
              />
            </label>
            <label className="grid gap-1 text-sm">
              <span className="text-slate-400">轮巡间隔（分钟）</span>
              <input
                type="number"
                min={1}
                className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                value={form.poll_interval_minutes}
                onChange={(event) =>
                  setForm((current) => ({ ...current, poll_interval_minutes: Number(event.target.value || 30) }))
                }
              />
            </label>
            <label className="grid gap-1 text-sm lg:col-span-2">
              <span className="text-slate-400">描述</span>
              <input
                className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                value={form.description}
                onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))}
              />
            </label>
            <label className="grid gap-1 text-sm lg:col-span-2">
              <span className="text-slate-400">面板 URL（JSON API）</span>
              <input
                className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                value={form.dashboard_url}
                onChange={(event) => setForm((current) => ({ ...current, dashboard_url: event.target.value }))}
                placeholder="https://example.com/api/bugs"
              />
            </label>
            <label className="grid gap-1 text-sm">
              <span className="text-slate-400">请求方法</span>
              <select
                className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                value={form.request_method}
                onChange={(event) =>
                  setForm((current) => ({ ...current, request_method: event.target.value as WatcherRequestMethod }))
                }
              >
                <option value="GET">GET</option>
                <option value="POST">POST</option>
              </select>
            </label>
            <label className="grid gap-1 text-sm">
              <span className="text-slate-400">全局发件邮箱</span>
              <input
                readOnly
                className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-slate-400"
                value={mailSettings?.sender_email ?? ""}
                placeholder="请先到设置 -> 邮箱设置配置 SMTP 用户名"
              />
            </label>
            <label className="grid gap-1 text-sm">
              <span className="text-slate-400">收件邮箱</span>
              <input
                className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                value={form.recipient_emails_text}
                onChange={(event) => setForm((current) => ({ ...current, recipient_emails_text: event.target.value }))}
                placeholder="a@example.com, b@example.com"
              />
            </label>
            <div className="rounded-2xl border border-slate-800 bg-slate-950/40 px-4 py-3 text-sm text-slate-400 lg:col-span-2">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="font-medium text-slate-200">邮件通知说明</div>
                  <div className="mt-1 text-xs leading-6 text-slate-500">
                    巡检 Agent 统一使用全局邮箱设置发信；接口检查不依赖邮箱设置。
                  </div>
                </div>
                <button
                  className="rounded-xl border border-slate-700 px-3 py-2 text-xs text-slate-200 hover:border-slate-500"
                  onClick={openMailSettings}
                  type="button"
                >
                  去邮箱设置
                </button>
              </div>
              {(mailSettings == null || mailSettings.sender_email.trim() === "" || !mailSettings.enabled) && (
                <div className="mt-3 text-xs leading-6 text-amber-300">
                  当前还没有可用的全局发件邮箱；立即运行时如果链路走到发邮件节点，会提示你先完成邮箱设置。
                </div>
              )}
            </div>
            <label className="grid gap-1 text-sm lg:col-span-2">
              <span className="text-slate-400">Cookie</span>
              <textarea
                className="min-h-36 rounded-2xl border border-slate-700 bg-slate-950 px-3 py-3 font-mono text-xs leading-6"
                value={form.request_cookie}
                onChange={(event) => setForm((current) => ({ ...current, request_cookie: event.target.value }))}
                placeholder="tenant_info=0000; yht_access_token=...; ycap_xxx=..."
              />
            </label>
            <label className="grid gap-1 text-sm lg:col-span-2">
              <span className="text-slate-400">附加请求头 JSON（可选）</span>
              <textarea
                className="min-h-28 rounded-2xl border border-slate-700 bg-slate-950 px-3 py-3 font-mono text-xs leading-6"
                value={form.request_extra_headers_text}
                onChange={(event) =>
                  setForm((current) => ({ ...current, request_extra_headers_text: event.target.value }))
                }
                placeholder='{"Origin":"https://pm.yyrd.com","Referer":"https://pm.yyrd.com/"}'
              />
            </label>
            {form.request_method === "POST" && (
              <label className="grid gap-1 text-sm lg:col-span-2">
                <span className="text-slate-400">请求体</span>
                <textarea
                  className="min-h-40 rounded-2xl border border-slate-700 bg-slate-950 px-3 py-3 font-mono text-xs leading-6"
                  value={form.request_body_text}
                  onChange={(event) => setForm((current) => ({ ...current, request_body_text: event.target.value }))}
                  placeholder={'{"pageNumber":1,"pageSize":30}\n\n或\n\nstartIndex=0&filterId=-1&jql=...'}
                />
                <div className="text-xs leading-6 text-slate-500">
                  如果 `Content-Type` 是 `application/json`，这里会按 JSON 对象校验；如果是 `application/x-www-form-urlencoded` 等类型，会按原始文本直接发送。
                </div>
              </label>
            )}
            <div className="rounded-2xl border border-slate-800 bg-slate-950/40 px-4 py-4 text-sm text-slate-400 lg:col-span-2">
              <div className="font-medium text-slate-200">详情请求模板（可选）</div>
              <div className="mt-2 text-xs leading-6 text-slate-500">
                如果这里配置了详情接口，巡检会在主列表解析完成后，按每条 Bug 的 `bug_id` 逐条请求详情。
                支持占位符：<code>{'{{bug_id}}'}</code>、<code>{'{{issue_key}}'}</code>、<code>{'{{timestamp_ms}}'}</code>。Jira 自动转派会先从详情接口提取 `issue id / atl_token`，如果缺少 `formToken` 会再自动补抓一次 `AssignIssue` 页面。
              </div>
            </div>
            <label className="grid gap-1 text-sm lg:col-span-2">
              <span className="text-slate-400">详情 URL 模板</span>
              <input
                className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-xs"
                value={form.detail_url_template}
                onChange={(event) => setForm((current) => ({ ...current, detail_url_template: event.target.value }))}
                placeholder="https://gfjira.yyrd.com/secure/AjaxIssueAction!default.jspa?issueKey={{bug_id}}&decorator=none&_={{timestamp_ms}}"
              />
            </label>
            <label className="grid gap-1 text-sm">
              <span className="text-slate-400">详情请求方法</span>
              <select
                className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                value={form.detail_request_method}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    detail_request_method: event.target.value as WatcherRequestMethod,
                  }))
                }
              >
                <option value="GET">GET</option>
                <option value="POST">POST</option>
              </select>
            </label>
            <label className="grid gap-1 text-sm">
              <span className="text-slate-400">详情 Cookie</span>
              <input
                className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-xs"
                value={form.detail_request_cookie}
                onChange={(event) => setForm((current) => ({ ...current, detail_request_cookie: event.target.value }))}
                placeholder="留空则不额外附带 Cookie"
              />
            </label>
            <label className="grid gap-1 text-sm lg:col-span-2">
              <span className="text-slate-400">详情附加请求头 JSON</span>
              <textarea
                className="min-h-28 rounded-2xl border border-slate-700 bg-slate-950 px-3 py-3 font-mono text-xs leading-6"
                value={form.detail_request_extra_headers_text}
                onChange={(event) =>
                  setForm((current) => ({ ...current, detail_request_extra_headers_text: event.target.value }))
                }
                placeholder='{"Referer":"https://gfjira.yyrd.com/browse/{{bug_id}}","x-requested-with":"XMLHttpRequest"}'
              />
            </label>
            {form.detail_request_method === "POST" && (
              <label className="grid gap-1 text-sm lg:col-span-2">
                <span className="text-slate-400">详情请求体</span>
                <textarea
                  className="min-h-28 rounded-2xl border border-slate-700 bg-slate-950 px-3 py-3 font-mono text-xs leading-6"
                  value={form.detail_request_body_text}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, detail_request_body_text: event.target.value }))
                  }
                  placeholder="issueKey={{bug_id}}"
                />
              </label>
            )}
            <div className="lg:col-span-2">
              <div className="rounded-2xl border border-slate-800 bg-slate-950/40 px-4 py-4">
                <div className="text-sm font-medium text-slate-200">运行模式</div>
                <div className="mt-2 grid gap-3 md:grid-cols-2">
                  <button
                    type="button"
                    className={
                      "rounded-2xl border px-4 py-4 text-left transition " +
                      (form.match_mode === "llm_fallback"
                        ? "border-amber-300/60 bg-amber-300/10"
                        : "border-slate-700 bg-slate-950 hover:border-slate-500")
                    }
                    onClick={() => setForm((current) => ({ ...current, match_mode: "llm_fallback" }))}
                  >
                    <div className="font-medium text-slate-100">规则 + LLM 兜底</div>
                    <div className="mt-1 text-xs leading-6 text-slate-400">
                      先按服务、模块、标题关键词、客户问题类型命中规则；没命中时再进入大模型兜底。
                    </div>
                  </button>
                  <button
                    type="button"
                    className={
                      "rounded-2xl border px-4 py-4 text-left transition " +
                      (form.match_mode === "fixed_match"
                        ? "border-sky-300/60 bg-sky-300/10"
                        : "border-slate-700 bg-slate-950 hover:border-slate-500")
                    }
                    onClick={() => setForm((current) => ({ ...current, match_mode: "fixed_match" }))}
                  >
                    <div className="font-medium text-slate-100">固定匹配</div>
                    <div className="mt-1 text-xs leading-6 text-slate-400">
                      只用列表 + 详情数据做规则命中，不调用大模型。关键词只匹配标题，客户问题类型直接读取详情数据。
                    </div>
                  </button>
                </div>
                {form.match_mode === "llm_fallback" ? (
                  <div className="mt-4">
                    <ModelSelector
                      value={form.model_config}
                      onChange={(nextModelConfig) => setForm((current) => ({ ...current, model_config: nextModelConfig }))}
                    />
                  </div>
                ) : (
                  <div className="mt-4 rounded-2xl border border-sky-400/20 bg-sky-400/5 px-4 py-3 text-xs leading-6 text-sky-100">
                    当前为固定匹配模式，保存和运行时不会校验模型配置，也不会调用大模型解析或兜底分配。
                  </div>
                )}
              </div>
            </div>
          </div>

          <label className="mt-4 flex items-center gap-3 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(event) => setForm((current) => ({ ...current, enabled: event.target.checked }))}
            />
            启用轮巡调度
          </label>

          {form.id != null && (
            <div className="mt-4 rounded-2xl border border-slate-800 bg-slate-950/40 px-4 py-3 text-sm text-slate-400">
              <div className="grid gap-2">
                <div>连续失败次数：{selectedWatcher?.consecutive_failure_count ?? 0}</div>
                <div>自动停用时间：{formatDate(selectedWatcher?.auto_disabled_at ?? null)}</div>
                <div className="whitespace-pre-wrap break-words">
                  自动停用原因：{selectedWatcher?.auto_disabled_reason || "-"}
                </div>
              </div>
            </div>
          )}

          <div className="mt-6">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-medium text-slate-200">经办人规则</div>
                <div className="mt-1 text-xs text-slate-500">
                  {form.match_mode === "fixed_match"
                    ? "固定匹配模式下，只按服务、模块、标题关键词、客户问题类型命中规则。"
                    : "规则优先，未命中时才进入 LLM 兜底分配。"}
                </div>
              </div>
              <button
                className="rounded-xl border border-slate-700 px-3 py-2 text-sm hover:border-amber-300"
                onClick={() => setForm((current) => ({ ...current, owner_rules: current.owner_rules.concat(emptyRule()) }))}
              >
                添加规则
              </button>
            </div>

            <div className="mt-4 space-y-4">
              {form.owner_rules.map((rule, index) => (
                <div key={index} className="rounded-2xl border border-slate-800 bg-slate-950/40 p-4">
                  <div className="flex items-center justify-between">
                    <div className="text-sm font-medium text-slate-200">规则 #{index + 1}</div>
                    {form.owner_rules.length > 1 && (
                      <button
                        className="text-xs text-rose-300"
                        onClick={() =>
                          setForm((current) => ({
                            ...current,
                            owner_rules: current.owner_rules.filter((_, ruleIndex) => ruleIndex !== index)
                          }))
                        }
                      >
                        删除
                      </button>
                    )}
                  </div>

                  <div className="mt-4 grid gap-3 lg:grid-cols-2">
                    <label className="grid gap-1 text-sm">
                      <span className="text-slate-400">转派目标</span>
                      <input
                        className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                        value={rule.assignee_code}
                        onChange={(event) =>
                          setForm((current) => ({
                            ...current,
                            owner_rules: current.owner_rules.map((item, ruleIndex) =>
                              ruleIndex === index ? { ...item, assignee_code: event.target.value } : item
                            )
                          }))
                        }
                        placeholder="qiangxiao 或 0000140558"
                      />
                    </label>
                    <label className="grid gap-1 text-sm">
                      <span className="text-slate-400">服务匹配词</span>
                      <input
                        className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                        value={rule.services_text}
                        onChange={(event) =>
                          setForm((current) => ({
                            ...current,
                            owner_rules: current.owner_rules.map((item, ruleIndex) =>
                              ruleIndex === index ? { ...item, services_text: event.target.value } : item
                            )
                          }))
                        }
                        placeholder="流程, billing"
                      />
                    </label>
                    <label className="grid gap-1 text-sm">
                      <span className="text-slate-400">模块匹配词</span>
                      <input
                        className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                        value={rule.modules_text}
                        onChange={(event) =>
                          setForm((current) => ({
                            ...current,
                            owner_rules: current.owner_rules.map((item, ruleIndex) =>
                              ruleIndex === index ? { ...item, modules_text: event.target.value } : item
                            )
                          }))
                        }
                        placeholder="工作台, dashboard"
                      />
                    </label>
                    <label className="grid gap-1 text-sm">
                      <span className="text-slate-400">客户问题类型</span>
                      <input
                        className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                        value={rule.customer_issue_types_text}
                        onChange={(event) =>
                          setForm((current) => ({
                            ...current,
                            owner_rules: current.owner_rules.map((item, ruleIndex) =>
                              ruleIndex === index ? { ...item, customer_issue_types_text: event.target.value } : item
                            )
                          }))
                        }
                        placeholder="性能问题、数据异常"
                      />
                    </label>
                    <label className="grid gap-1 text-sm lg:col-span-2">
                      <span className="text-slate-400">关键词</span>
                      <input
                        className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                        value={rule.keywords_text}
                        onChange={(event) =>
                          setForm((current) => ({
                            ...current,
                            owner_rules: current.owner_rules.map((item, ruleIndex) =>
                              ruleIndex === index ? { ...item, keywords_text: event.target.value } : item
                            )
                          }))
                        }
                        placeholder="异常, 卡顿, 超时"
                      />
                    </label>
                    <div className="rounded-2xl border border-slate-800 bg-slate-950/50 px-3 py-3 text-xs leading-6 text-slate-500 lg:col-span-2">
                      命中规则后，会自动把当前 Bug 转派给这里填写的目标。Jira 场景请直接填写用户名，例如 `qiangxiao`；PM 场景仍可填写经办人编码。客户问题类型支持按 `、`、中文逗号或英文逗号分隔。
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {error !== "" && <div className="mt-6 text-sm text-rose-300 whitespace-pre-wrap">{error}</div>}
      </main>

      <aside className="bg-slate-900/60 p-5 xl:min-h-0 xl:overflow-y-auto">
        <div className="rounded-3xl border border-slate-800 bg-slate-900 p-5">
          <div className="text-sm text-slate-400">接口检查与最近运行记录</div>
          <h3 className="mt-1 text-xl font-semibold">巡检结果</h3>

          {testResult != null && (
            <div className="mt-5 rounded-2xl border border-slate-800 bg-slate-950/40 p-4">
              <div className="flex items-center justify-between gap-3">
                <div className="font-medium text-slate-100">接口检查</div>
                <div className={"text-xs " + (testResult.ok ? "text-emerald-300" : "text-rose-300")}>
                  HTTP {testResult.status_code}
                </div>
              </div>
              <div className="mt-3 grid gap-2 text-xs text-slate-400">
                <div>方法：{testResult.request_method}</div>
                <div className="break-all">URL：{testResult.dashboard_url}</div>
                <div>Cookie：{previewCookie(testResult.request_headers.Cookie ?? testResult.request_headers.cookie ?? "")}</div>
                <div>
                  附加头：
                  {Object.keys(stripCookieHeader(testResult.request_headers)).length > 0
                    ? Object.keys(stripCookieHeader(testResult.request_headers)).join(", ")
                    : "无"}
                </div>
                <div>解析计数：{testResult.parsed_item_count}</div>
                <div>解析到 Bug：{testResult.parsed_bug_count}</div>
                <div>响应类型：{testResult.response_content_type || "-"}</div>
              </div>
              {testResult.parsed_bug_preview.length > 0 && (
                <>
                  <div className="mt-3 text-xs font-medium text-slate-300">解析预览</div>
                  <div className="mt-2 space-y-2">
                    {testResult.parsed_bug_preview.map((bug) => (
                      <div key={bug.bug_id} className="rounded-xl border border-slate-800 bg-slate-950/60 px-3 py-3 text-xs text-slate-300">
                        <div className="font-medium text-slate-100">
                          [{bug.bug_id}] {bug.title || "(无标题)"}
                        </div>
                        <div className="mt-1 text-slate-400">
                          辅助 ID：{bug.jira_issue_id || bug.bug_aid || "-"} · 服务模块：{bug.service || "-"} / {bug.module || "-"} · 客户问题类型：
                          {bug.customer_issue_type || "-"} · 状态：{bug.status || "-"} · 经办人：{bug.assignee || "-"}
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              )}
              {Object.keys(stripCookieHeader(testResult.request_headers)).length > 0 && (
                <>
                  <div className="mt-3 text-xs font-medium text-slate-300">附加请求头预览</div>
                  <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-xl border border-slate-800 bg-slate-950/60 px-3 py-3 text-xs leading-6 text-slate-400">
                    {stringifyJson(stripCookieHeader(testResult.request_headers))}
                  </pre>
                </>
              )}
              {testResult.request_method === "POST" && (
                <>
                  <div className="mt-3 text-xs font-medium text-slate-300">请求体预览</div>
                  <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-xl border border-slate-800 bg-slate-950/60 px-3 py-3 text-xs leading-6 text-slate-400">
                    {testResult.request_body_text?.trim()
                      ? testResult.request_body_text
                      : testResult.request_body_json
                        ? stringifyJson(testResult.request_body_json)
                        : "(空请求体)"}
                  </pre>
                </>
              )}
              <div className="mt-3 rounded-xl border border-slate-800 bg-slate-950/60 px-3 py-3 text-xs leading-6 text-slate-400">
                {testResult.message}
              </div>
              <div className="mt-3 text-xs font-medium text-slate-300">响应预览</div>
              <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-xl border border-slate-800 bg-slate-950/60 px-3 py-3 text-xs leading-6 text-slate-400">{testResult.response_body_preview || "(空响应)"}</pre>
            </div>
          )}

          <div className="mt-4 space-y-3">
            {runs.map((run) => (
              <button
                key={run.id}
                className={
                  "w-full rounded-2xl border p-4 text-left transition " +
                  (selectedRun?.id === run.id
                    ? "border-sky-400/60 bg-sky-400/10"
                    : "border-slate-800 bg-slate-950/40 hover:border-slate-700")
                }
                onClick={() => setSelectedRunId(run.id)}
              >
                <div className="flex items-center justify-between gap-3">
                  <div className={"text-sm font-medium " + statusTone(run.status)}>{run.status}</div>
                  <div className="text-xs text-slate-500">{formatDate(run.started_at)}</div>
                </div>
                <div className="mt-2 text-xs text-slate-400">
                  抓取 {run.fetched_count} · 解析 {run.parsed_count} · 新增 {run.new_bug_count} · 分配成功 {run.assigned_count}
                </div>
                <div className="mt-2 line-clamp-2 text-xs text-slate-500">{run.summary}</div>
              </button>
            ))}
            {runs.length === 0 && <div className="text-sm text-slate-500">还没有运行记录，保存后可手动运行一次。</div>}
          </div>

          {selectedRun != null && (
            <div className="mt-6 space-y-4">
              <div className="rounded-2xl border border-slate-800 bg-slate-950/40 p-4">
                <div className="flex items-center justify-between">
                  <div className="font-medium">运行摘要</div>
                  <div className={"text-sm " + statusTone(selectedRun.status)}>{selectedRun.status}</div>
                </div>
                <div className="mt-3 grid gap-2 text-xs text-slate-400">
                  <div>开始时间：{formatDate(selectedRun.started_at)}</div>
                  <div>结束时间：{formatDate(selectedRun.ended_at)}</div>
                  <div>邮件发送：{selectedRun.emailed ? "是" : "否"}</div>
                </div>
                <div className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-300">{selectedRun.summary}</div>
                {selectedRun.error_message && (
                  <div className="mt-3 rounded-xl border border-rose-400/30 bg-rose-400/10 px-3 py-3 text-xs leading-6 text-rose-200">
                    {selectedRun.error_message}
                  </div>
                )}
              </div>

              <div>
                <div className="text-sm font-medium text-slate-200">新增 Bug 与分配结果</div>
                <div className="mt-3 space-y-3">
                  {selectedRun.assignment_results.map((item: WatcherAssignmentResult) => (
                    <div key={item.bug_id} className="rounded-2xl border border-slate-800 bg-slate-950/40 p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div className="font-medium text-slate-100">
                          [{item.bug_id}] {item.title || "未提供标题"}
                        </div>
                        <div className={"text-xs " + statusTone(item.assignment_status === "success" ? "success" : item.assignment_status === "failed" ? "failed" : "no_change")}>
                          {item.assignment_status}
                        </div>
                      </div>
                      <div className="mt-2 text-xs text-slate-500">
                        {item.service || "-"} / {item.module || "-"} / {item.status || "-"}
                      </div>
                      <div className="mt-2 text-xs text-slate-400">
                        辅助 ID：{item.jira_issue_id || item.bug_aid || "-"} · 转派目标：{item.assignee_code || "未匹配"}
                      </div>
                      <div className="mt-1 text-xs text-slate-400">
                        来源：{item.match_source} · 原因：{item.match_reason || "-"}
                      </div>
                      {item.assignment_message && <div className="mt-2 text-xs text-slate-500">{item.assignment_message}</div>}
                    </div>
                  ))}
                  {selectedRun.assignment_results.length === 0 && (
                    <div className="text-sm text-slate-500">当前这次运行没有进入“新增 Bug 分配”阶段。</div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}

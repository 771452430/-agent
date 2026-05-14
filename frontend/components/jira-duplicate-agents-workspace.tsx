"use client";

/**
 * Jira 重复工单审核 Agent 工作区。
 *
 * v1 聚焦人工审核：抓当前待处理 Jira，匹配本地已完成工单，并展示候选解决方案。
 */
import { useEffect, useMemo, useState } from "react";

import {
  createJiraDuplicateAgent,
  getJiraDuplicateAgent,
  listJiraDuplicateAgents,
  listJiraDuplicateRuns,
  reindexJiraDuplicateAgent,
  runJiraDuplicateAgent,
  testJiraDuplicateFetch,
  updateJiraDuplicateAgent
} from "../lib/api";
import type {
  JiraDuplicateAgentConfig,
  JiraDuplicateFetchTestResponse,
  JiraDuplicateIssueResult,
  JiraDuplicateMatchLevel,
  JiraDuplicateRun,
  ModelConfig,
  WatcherRequestMethod
} from "../lib/types";
import { ModelSelector } from "./model-selector";
import { useModelSettings } from "./model-settings-provider";

const DEFAULT_SOURCE_DB_PATH = "/Users/wangyahui/yonyou/AI工具/jira-data-query/jiradata/jira_support.db";

const DEFAULT_MODEL: ModelConfig = {
  mode: "learning",
  provider: "mock",
  model: "learning-mode",
  temperature: 0.2,
  max_tokens: 1024
};

type JiraDuplicateFormState = {
  id?: string;
  name: string;
  description: string;
  source_db_path: string;
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
  poll_interval_minutes: number;
  high_similarity_threshold: number;
  medium_similarity_threshold: number;
  model_review_enabled: boolean;
  model_config: ModelConfig;
  enabled: boolean;
};

function formatDate(value?: string | null) {
  if (value == null || value === "") return "-";
  return new Date(value).toLocaleString("zh-CN");
}

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

function previewCookie(cookie: string) {
  const normalized = cookie.trim();
  if (normalized === "") return "未配置";
  if (normalized.length <= 96) return normalized;
  return normalized.slice(0, 96) + "...";
}

function readCookieHeader(headers: Record<string, string>): string {
  return headers.Cookie ?? headers.cookie ?? "";
}

function stripCookieHeader(headers: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(headers).filter(([key]) => key.toLowerCase() !== "cookie"));
}

function readHeaderValue(headers: Record<string, string>, name: string): string {
  const normalizedName = name.trim().toLowerCase();
  for (const [key, value] of Object.entries(headers)) {
    if (key.trim().toLowerCase() === normalizedName) return String(value ?? "").trim();
  }
  return "";
}

function looksLikeFormUrlEncodedBody(bodyText: string): boolean {
  const normalized = bodyText.trim();
  if (normalized === "" || normalized.startsWith("{") || normalized.startsWith("[")) return false;
  return normalized.includes("=") && (normalized.includes("&") || normalized.includes("%") || normalized.includes("+"));
}

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
      result += `"${segment.replaceAll("\\", "\\\\").replaceAll('"', '\\"')}"`;
      continue;
    }
    result += command[index];
    index += 1;
  }
  return result;
}

function normalizeMethod(value?: string | null): WatcherRequestMethod {
  return value?.toUpperCase() === "POST" ? "POST" : "GET";
}

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
      if (char === "'") quote = null;
      else current += char;
      continue;
    }
    if (quote === '"') {
      if (char === '"') quote = null;
      else current += char;
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
  if (current !== "") tokens.push(current);
  return tokens;
}

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
        if (name.toLowerCase() === "cookie") requestCookie = value;
        else if (name !== "") requestHeaders[name] = value;
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
      if (requestMethod === "GET") requestMethod = "POST";
      index += 1;
      continue;
    }
    if (!token.startsWith("-") && dashboardUrl === "") {
      dashboardUrl = token;
    }
  }

  if (dashboardUrl.trim() === "") throw new Error("没有从 curl 里解析到 URL。");
  return {
    dashboard_url: dashboardUrl.trim(),
    request_method: requestMethod,
    request_cookie: requestCookie.trim(),
    request_extra_headers_text: Object.keys(requestHeaders).length > 0 ? stringifyJson(requestHeaders) : "",
    request_body_text: requestBodyText.trim()
  };
}

function normalizeDetailTemplateText(value: string): string {
  return value
    .replace(/\b[A-Z][A-Z0-9]+-\d+\b/g, "{{issue_key}}")
    .replace(/([?&]_)=\d{8,}/g, "$1={{timestamp_ms}}");
}

function buildEmptyForm(): JiraDuplicateFormState {
  return {
    name: "新的 Jira 工单 Agent",
    description: "",
    source_db_path: DEFAULT_SOURCE_DB_PATH,
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
    poll_interval_minutes: 30,
    high_similarity_threshold: 0.78,
    medium_similarity_threshold: 0.55,
    model_review_enabled: false,
    model_config: DEFAULT_MODEL,
    enabled: true
  };
}

function agentToForm(agent: JiraDuplicateAgentConfig): JiraDuplicateFormState {
  return {
    id: agent.id,
    name: agent.name,
    description: agent.description,
    source_db_path: agent.source_db_path,
    dashboard_url: agent.dashboard_url,
    request_method: agent.request_method,
    request_cookie: readCookieHeader(agent.request_headers),
    request_extra_headers_text:
      Object.keys(stripCookieHeader(agent.request_headers)).length > 0
        ? stringifyJson(stripCookieHeader(agent.request_headers))
        : "",
    request_body_text:
      agent.request_body_text != null && agent.request_body_text.trim() !== ""
        ? agent.request_body_text
        : agent.request_body_json
          ? stringifyJson(agent.request_body_json)
          : "",
    detail_url_template: agent.detail_url_template ?? "",
    detail_request_method: agent.detail_request_method,
    detail_request_cookie: readCookieHeader(agent.detail_request_headers),
    detail_request_extra_headers_text:
      Object.keys(stripCookieHeader(agent.detail_request_headers)).length > 0
        ? stringifyJson(stripCookieHeader(agent.detail_request_headers))
        : "",
    detail_request_body_text: agent.detail_request_body_text ?? "",
    poll_interval_minutes: agent.poll_interval_minutes,
    high_similarity_threshold: Math.min(agent.high_similarity_threshold, 0.78),
    medium_similarity_threshold: Math.min(agent.medium_similarity_threshold, 0.55),
    model_review_enabled: agent.model_review_enabled,
    model_config: agent.model_config,
    enabled: agent.enabled
  };
}

function statusTone(status?: string | null) {
  switch (status) {
    case "success":
      return "text-emerald-300";
    case "no_change":
      return "text-sky-300";
    case "partial_success":
      return "text-amber-300";
    case "failed":
      return "text-rose-300";
    default:
      return "text-slate-400";
  }
}

function matchBadge(level: JiraDuplicateMatchLevel) {
  switch (level) {
    case "high":
      return "border-emerald-300/40 bg-emerald-400/10 text-emerald-100";
    case "medium":
      return "border-amber-300/40 bg-amber-400/10 text-amber-100";
    case "low":
      return "border-slate-400/30 bg-slate-400/10 text-slate-200";
    default:
      return "border-slate-500/30 bg-slate-500/10 text-slate-300";
  }
}

function matchLabel(level: JiraDuplicateMatchLevel) {
  if (level === "high") return "建议复用";
  if (level === "medium") return "需人工判断";
  if (level === "low") return "弱相似";
  return "未命中";
}

function buildRequestPayload(form: JiraDuplicateFormState) {
  const requestHeaders = buildRequestHeaders(form.request_extra_headers_text, form.request_cookie, "附加请求头 JSON");
  const normalizedBodyText = form.request_body_text.trim();
  const bodyMode = resolveRequestBodyMode(requestHeaders, normalizedBodyText);
  const requestBodyJson =
    form.request_method === "POST" && bodyMode === "json" && normalizedBodyText !== ""
      ? parseJsonObjectInput(normalizedBodyText, "请求体 JSON")
      : null;
  const requestBodyText =
    form.request_method === "POST" && bodyMode === "raw" && normalizedBodyText !== ""
      ? normalizedBodyText
      : null;

  const detailHeaders =
    form.detail_url_template.trim() === ""
      ? {}
      : buildRequestHeaders(form.detail_request_extra_headers_text, form.detail_request_cookie, "详情附加请求头 JSON");

  return {
    name: form.name.trim(),
    description: form.description.trim(),
    source_db_path: form.source_db_path.trim(),
    dashboard_url: form.dashboard_url.trim(),
    request_method: form.request_method,
    request_headers: requestHeaders,
    request_body_json: requestBodyJson,
    request_body_text: requestBodyText,
    detail_url_template: form.detail_url_template.trim() !== "" ? form.detail_url_template.trim() : null,
    detail_request_method: form.detail_url_template.trim() !== "" ? form.detail_request_method : "GET",
    detail_request_headers: detailHeaders,
    detail_request_body_text:
      form.detail_url_template.trim() !== "" &&
      form.detail_request_method === "POST" &&
      form.detail_request_body_text.trim() !== ""
        ? form.detail_request_body_text.trim()
        : null,
    poll_interval_minutes: form.poll_interval_minutes,
    high_similarity_threshold: form.high_similarity_threshold,
    medium_similarity_threshold: form.medium_similarity_threshold,
    model_review_enabled: form.model_review_enabled,
    model_config: form.model_config,
    enabled: form.enabled
  };
}

function buildRequestHeaders(extraHeadersText: string, cookie: string, label: string): Record<string, string> {
  const headers = parseJsonObjectInput(extraHeadersText, label);
  const normalized: Record<string, string> = {};
  for (const [key, value] of Object.entries(headers)) {
    normalized[key] = String(value);
  }
  if (cookie.trim() !== "") normalized.Cookie = cookie.trim();
  return normalized;
}

function sortIssueResultsByScore(results: JiraDuplicateIssueResult[]) {
  return results.slice().sort((left, right) => {
    if (right.match_score !== left.match_score) return right.match_score - left.match_score;
    return left.issue_key.localeCompare(right.issue_key);
  });
}

function IssueResultCard(props: { result: JiraDuplicateIssueResult }) {
  const { result } = props;
  const sortedCandidates = result.candidates.slice().sort((left, right) => {
    if (right.score !== left.score) return right.score - left.score;
    return left.issue_key.localeCompare(right.issue_key);
  });
  return (
    <article className="rounded-[20px] border border-white/10 bg-white/[0.04] p-4">
      <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-sm text-sky-200">{result.issue_key}</span>
            <span className={"rounded-full border px-2.5 py-1 text-xs " + matchBadge(result.match_level)}>
              {matchLabel(result.match_level)} · {(result.match_score * 100).toFixed(0)}%
            </span>
            {result.status && <span className="text-xs text-slate-400">{result.status}</span>}
          </div>
          <h3 className="mt-2 break-words text-base font-semibold text-white">{result.title || "未解析到标题"}</h3>
          <div className="mt-2 flex flex-wrap gap-2 text-xs text-slate-400">
            <span>领域：{result.domain || "-"}</span>
            <span>模块：{result.module || "-"}</span>
          </div>
          <p className="mt-2 line-clamp-3 text-sm leading-6 text-slate-300">{result.description || result.raw_excerpt || "-"}</p>
        </div>
        <div className="shrink-0 rounded-2xl border border-white/10 px-3 py-2 text-xs text-slate-300">
          {result.match_reason}
        </div>
      </div>

      {sortedCandidates.length > 0 ? (
        <div className="mt-4 space-y-3">
          {sortedCandidates.map((candidate) => (
            <div key={candidate.issue_key} className="rounded-2xl border border-white/10 bg-slate-950/30 p-4">
              <div className="flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="font-mono text-sky-200">{candidate.issue_key}</span>
                    <span className="rounded-full border border-white/10 px-2 py-0.5 text-slate-300">
                      {(candidate.score * 100).toFixed(0)}%
                    </span>
                    <span className="text-slate-500">{candidate.status}</span>
                  </div>
                  <div className="mt-2 break-words text-sm font-medium text-slate-100">{candidate.summary}</div>
                  <div className="mt-1 text-xs text-slate-500">
                    {candidate.domain || "-"} / {candidate.module || "-"} · {candidate.reason}
                  </div>
                </div>
              </div>
              <div className="mt-3 whitespace-pre-wrap rounded-xl bg-black/20 p-3 text-sm leading-6 text-slate-200">
                {candidate.solution}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-4 rounded-2xl border border-dashed border-white/10 p-4 text-sm text-slate-400">
          未找到达到展示阈值的已完成工单解决方案。
        </div>
      )}
    </article>
  );
}

export function JiraDuplicateAgentsWorkspace() {
  const { validateModelConfig } = useModelSettings();
  const [agents, setAgents] = useState<JiraDuplicateAgentConfig[]>([]);
  const [runs, setRuns] = useState<JiraDuplicateRun[]>([]);
  const [form, setForm] = useState<JiraDuplicateFormState>(buildEmptyForm());
  const [curlDraft, setCurlDraft] = useState("");
  const [detailCurlDraft, setDetailCurlDraft] = useState("");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<JiraDuplicateFetchTestResponse | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [isBootstrapping, setIsBootstrapping] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [isReindexing, setIsReindexing] = useState(false);
  const [isConfigOpen, setIsConfigOpen] = useState(false);
  const [activeResultGroup, setActiveResultGroup] = useState<"high" | "medium" | "none">("high");

  const modelValidation = validateModelConfig(form.model_config);
  const selectedRun = useMemo(
    () => runs.find((item) => item.id === selectedRunId) ?? runs[0] ?? null,
    [runs, selectedRunId]
  );
  const currentIssues = selectedRun?.issue_results ?? [];
  const groupedResults = useMemo(
    () => ({
      high: sortIssueResultsByScore(currentIssues.filter((item) => item.match_level === "high")),
      medium: sortIssueResultsByScore(currentIssues.filter((item) => item.match_level === "medium")),
      none: sortIssueResultsByScore(currentIssues.filter((item) => item.match_level === "low" || item.match_level === "none"))
    }),
    [currentIssues]
  );
  const visibleResults = groupedResults[activeResultGroup];
  const sortedCurrentIssues = useMemo(() => sortIssueResultsByScore(currentIssues), [currentIssues]);

  async function loadAgentDetail(agentId: string) {
    const [agent, agentRuns] = await Promise.all([getJiraDuplicateAgent(agentId), listJiraDuplicateRuns(agentId)]);
    setForm(agentToForm(agent));
    setRuns(agentRuns);
    setSelectedRunId(agentRuns[0]?.id ?? null);
    setTestResult(null);
    setIsConfigOpen(false);
  }

  async function bootstrap() {
    setIsBootstrapping(true);
    setError("");
    try {
      const list = await listJiraDuplicateAgents();
      setAgents(list);
      if (list.length > 0) {
        await loadAgentDetail(list[0].id);
      } else {
        setForm(buildEmptyForm());
        setRuns([]);
        setSelectedRunId(null);
        setIsConfigOpen(true);
      }
    } finally {
      setIsBootstrapping(false);
    }
  }

  useEffect(() => {
    if (visibleResults.length > 0) return;
    if (groupedResults.high.length > 0) {
      setActiveResultGroup("high");
      return;
    }
    if (groupedResults.medium.length > 0) {
      setActiveResultGroup("medium");
      return;
    }
    setActiveResultGroup("none");
  }, [groupedResults, visibleResults.length]);

  useEffect(() => {
    void bootstrap().catch((cause) => {
      setError(String(cause));
      setIsBootstrapping(false);
    });
  }, []);

  function applyCurl(kind: "list" | "detail") {
    setError("");
    try {
      const parsed = parseCurlCommand(kind === "list" ? curlDraft : detailCurlDraft);
      if (kind === "list") {
        setForm((current) => ({
          ...current,
          dashboard_url: parsed.dashboard_url,
          request_method: parsed.request_method,
          request_cookie: parsed.request_cookie,
          request_extra_headers_text: parsed.request_extra_headers_text,
          request_body_text: parsed.request_body_text
        }));
        return;
      }
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
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function handleSave(selectId?: string) {
    setError("");
    setNotice("");
    if (form.name.trim() === "" || form.dashboard_url.trim() === "") {
      setError("名称和列表接口 URL 不能为空。");
      return null;
    }
    if (!modelValidation.isRunnable) {
      setError(modelValidation.message);
      return null;
    }
    setIsSaving(true);
    try {
      const payload = buildRequestPayload(form);
      const saved = form.id
        ? await updateJiraDuplicateAgent(form.id, payload)
        : await createJiraDuplicateAgent(payload);
      const list = await listJiraDuplicateAgents();
      setAgents(list);
      await loadAgentDetail(selectId ?? saved.id);
      setIsConfigOpen(false);
      setNotice("配置已保存。");
      return saved.id;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return null;
    } finally {
      setIsSaving(false);
    }
  }

  async function handleTestFetch() {
    const agentId = form.id ?? (await handleSave());
    if (!agentId) return;
    setIsTesting(true);
    setError("");
    setNotice("");
    try {
      const result = await testJiraDuplicateFetch(agentId);
      setTestResult(result);
      setNotice(result.ok ? "接口检查完成。" : result.message);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setIsTesting(false);
    }
  }

  async function handleRun() {
    const agentId = form.id ?? (await handleSave());
    if (!agentId) return;
    setIsRunning(true);
    setError("");
    setNotice("");
    try {
      const run = await runJiraDuplicateAgent(agentId);
      const latestRuns = await listJiraDuplicateRuns(agentId);
      const latestAgents = await listJiraDuplicateAgents();
      setRuns(latestRuns);
      setAgents(latestAgents);
      setSelectedRunId(run.id);
      setIsConfigOpen(false);
      setNotice("运行完成。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setIsRunning(false);
    }
  }

  async function handleReindex() {
    const agentId = form.id ?? (await handleSave());
    if (!agentId) return;
    setIsReindexing(true);
    setError("");
    setNotice("");
    try {
      const result = await reindexJiraDuplicateAgent(agentId);
      setNotice(`案例索引已重建：${result.indexed_count} 条，${result.embedding_backend}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setIsReindexing(false);
    }
  }

  async function handleToggle(agent: JiraDuplicateAgentConfig) {
    setError("");
    try {
      const updated = await updateJiraDuplicateAgent(agent.id, { enabled: !agent.enabled });
      const list = await listJiraDuplicateAgents();
      setAgents(list);
      if (form.id === updated.id) setForm(agentToForm(updated));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  function handleCreateNew() {
    setForm(buildEmptyForm());
    setRuns([]);
    setSelectedRunId(null);
    setTestResult(null);
    setError("");
    setNotice("");
    setIsConfigOpen(true);
  }

  if (isBootstrapping) {
    return <div className="p-6 text-sm text-slate-300">正在加载 Jira 工单 Agent...</div>;
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <div className="border-b border-white/10 px-6 py-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <div className="apple-kicker">Jira Duplicate Agent</div>
            <h2 className="mt-2 text-2xl font-semibold text-white">Jira 重复工单审核</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300">
              拉取当前待处理 Jira，与本地已完成工单做相似匹配，先把候选解决方案展示出来供人工审核。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="apple-button-secondary rounded-full px-4 py-2 text-sm" onClick={handleCreateNew}>
              新建
            </button>
            <button
              className="apple-button-secondary rounded-full px-4 py-2 text-sm"
              onClick={() => setIsConfigOpen((current) => !current)}
            >
              {isConfigOpen ? "收起配置" : "配置"}
            </button>
            <button className="apple-button-secondary rounded-full px-4 py-2 text-sm" disabled={isSaving} onClick={() => void handleSave()}>
              {isSaving ? "保存中..." : "保存"}
            </button>
            <button className="apple-button-secondary rounded-full px-4 py-2 text-sm" disabled={isTesting} onClick={() => void handleTestFetch()}>
              {isTesting ? "检查中..." : "检查接口"}
            </button>
            <button className="apple-button-secondary rounded-full px-4 py-2 text-sm" disabled={isReindexing} onClick={() => void handleReindex()}>
              {isReindexing ? "索引中..." : "重建索引"}
            </button>
            <button className="apple-button rounded-full px-4 py-2 text-sm" disabled={isRunning} onClick={() => void handleRun()}>
              {isRunning ? "运行中..." : "立即运行"}
            </button>
          </div>
        </div>
        {error && <div className="mt-4 rounded-2xl border border-rose-400/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-100">{error}</div>}
        {notice && <div className="mt-4 rounded-2xl border border-emerald-400/30 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-100">{notice}</div>}
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden xl:grid-cols-[320px_minmax(0,1fr)]">
        <aside className="min-h-0 overflow-y-auto border-b border-white/10 p-5 xl:border-b-0 xl:border-r">
          <div className="mb-3 flex items-center justify-between">
            <div className="text-sm font-medium text-slate-200">Agent 列表</div>
            <span className="text-xs text-slate-500">{agents.length} 个</span>
          </div>
          <div className="space-y-3">
            {agents.map((agent) => (
              <div
                key={agent.id}
                role="button"
                tabIndex={0}
                className={
                  "w-full cursor-pointer rounded-[22px] border p-4 text-left transition " +
                  (form.id === agent.id ? "border-sky-300/50 bg-sky-400/10" : "border-white/10 bg-white/[0.03] hover:bg-white/[0.06]")
                }
                onClick={() => void loadAgentDetail(agent.id).catch((cause) => setError(String(cause)))}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    void loadAgentDetail(agent.id).catch((cause) => setError(String(cause)));
                  }
                }}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-white">{agent.name}</div>
                    <div className="mt-1 text-xs text-slate-400">{agent.enabled ? "已启用" : "已停用"}</div>
                  </div>
                  <span className={"text-xs " + statusTone(agent.last_run_status)}>{agent.last_run_status ?? "-"}</span>
                </div>
                <div className="mt-3 space-y-1 text-xs text-slate-500">
                  <div>最近运行：{formatDate(agent.last_run_at)}</div>
                  <div>匹配数量：{agent.last_matched_count}</div>
                  <button
                    type="button"
                    className="mt-2 rounded-full border border-white/10 px-3 py-1 text-xs text-slate-300"
                    onClick={(event) => {
                      event.stopPropagation();
                      void handleToggle(agent);
                    }}
                  >
                    {agent.enabled ? "停用轮巡" : "启用轮巡"}
                  </button>
                </div>
              </div>
            ))}
            {agents.length === 0 && <div className="rounded-2xl border border-dashed border-white/10 p-4 text-sm text-slate-500">还没有配置。</div>}
          </div>
        </aside>

        <main className="min-h-0 overflow-y-auto p-5">
          <div className="space-y-5">
            {isConfigOpen && (
              <section className="space-y-5">
                <div className="grid gap-5 2xl:grid-cols-2">
                  <div className="apple-panel rounded-[24px] p-5">
                    <div className="text-sm font-medium text-slate-200">粘贴 curl 自动解析</div>
                    <textarea
                      className="mt-3 min-h-28 w-full rounded-2xl border border-white/10 bg-black/20 p-3 font-mono text-xs text-slate-200 outline-none focus:border-sky-300/50"
                      value={curlDraft}
                      onChange={(event) => setCurlDraft(event.target.value)}
                      placeholder={"curl 'https://jira.example.com/rest/...' \\\n  -H 'content-type: application/json' \\\n  -b 'JSESSIONID=...' \\\n  --data-raw '{\"jql\":\"status=待分析\"}'"}
                    />
                    <div className="mt-3 flex justify-end">
                      <button className="apple-button-secondary rounded-full px-4 py-2 text-sm" onClick={() => applyCurl("list")}>
                        解析列表 curl
                      </button>
                    </div>
                  </div>

                  <div className="apple-panel rounded-[24px] p-5">
                    <div className="text-sm font-medium text-slate-200">详情 curl（可选）</div>
                    <textarea
                      className="mt-3 min-h-24 w-full rounded-2xl border border-white/10 bg-black/20 p-3 font-mono text-xs text-slate-200 outline-none focus:border-sky-300/50"
                      value={detailCurlDraft}
                      onChange={(event) => setDetailCurlDraft(event.target.value)}
                      placeholder={"curl $'https://jira.example.com/secure/AjaxIssueAction...issueKey=YYZJ-138373' \\\n  -H 'x-requested-with: XMLHttpRequest' \\\n  -b 'JSESSIONID=...'"}
                    />
                    <div className="mt-3 flex justify-end">
                      <button className="apple-button-secondary rounded-full px-4 py-2 text-sm" onClick={() => applyCurl("detail")}>
                        解析详情 curl
                      </button>
                    </div>
                  </div>
                </div>

                <div className="grid gap-5 2xl:grid-cols-2">
                  <div className="apple-panel rounded-[24px] p-5">
                    <div className="grid gap-4 md:grid-cols-2">
                      <label className="block text-sm text-slate-300">
                        名称
                        <input
                          className="mt-2 w-full rounded-2xl border border-white/10 bg-black/20 px-3 py-2 text-sm text-white outline-none focus:border-sky-300/50"
                          value={form.name}
                          onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
                        />
                      </label>
                      <label className="block text-sm text-slate-300">
                        轮巡间隔（分钟）
                        <input
                          type="number"
                          min={1}
                          className="mt-2 w-full rounded-2xl border border-white/10 bg-black/20 px-3 py-2 text-sm text-white outline-none focus:border-sky-300/50"
                          value={form.poll_interval_minutes}
                          onChange={(event) =>
                            setForm((current) => ({
                              ...current,
                              poll_interval_minutes: Math.max(1, Number(event.target.value) || 1)
                            }))
                          }
                        />
                      </label>
                    </div>
                    <label className="mt-4 block text-sm text-slate-300">
                      本地 Jira DB
                      <input
                        className="mt-2 w-full rounded-2xl border border-white/10 bg-black/20 px-3 py-2 font-mono text-xs text-white outline-none focus:border-sky-300/50"
                        value={form.source_db_path}
                        onChange={(event) => setForm((current) => ({ ...current, source_db_path: event.target.value }))}
                      />
                    </label>
                    <label className="mt-4 block text-sm text-slate-300">
                      描述
                      <textarea
                        className="mt-2 min-h-20 w-full rounded-2xl border border-white/10 bg-black/20 px-3 py-2 text-sm text-white outline-none focus:border-sky-300/50"
                        value={form.description}
                        onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))}
                      />
                    </label>
                    <label className="mt-4 flex items-center gap-2 text-sm text-slate-300">
                      <input
                        type="checkbox"
                        checked={form.enabled}
                        onChange={(event) => setForm((current) => ({ ...current, enabled: event.target.checked }))}
                      />
                      启用定时轮巡
                    </label>
                  </div>

                  <div className="apple-panel rounded-[24px] p-5">
                    <div className="text-sm font-medium text-slate-200">请求配置</div>
                    <div className="mt-4 grid gap-4 md:grid-cols-[120px_minmax(0,1fr)]">
                      <label className="block text-sm text-slate-300">
                        方法
                        <select
                          className="mt-2 w-full rounded-2xl border border-white/10 bg-black/20 px-3 py-2 text-sm text-white outline-none"
                          value={form.request_method}
                          onChange={(event) => setForm((current) => ({ ...current, request_method: normalizeMethod(event.target.value) }))}
                        >
                          <option value="GET">GET</option>
                          <option value="POST">POST</option>
                        </select>
                      </label>
                      <label className="block text-sm text-slate-300">
                        列表接口 URL
                        <input
                          className="mt-2 w-full rounded-2xl border border-white/10 bg-black/20 px-3 py-2 font-mono text-xs text-white outline-none focus:border-sky-300/50"
                          value={form.dashboard_url}
                          onChange={(event) => setForm((current) => ({ ...current, dashboard_url: event.target.value }))}
                        />
                      </label>
                    </div>
                    <label className="mt-4 block text-sm text-slate-300">
                      Cookie
                      <textarea
                        className="mt-2 min-h-16 w-full rounded-2xl border border-white/10 bg-black/20 px-3 py-2 font-mono text-xs text-white outline-none focus:border-sky-300/50"
                        value={form.request_cookie}
                        onChange={(event) => setForm((current) => ({ ...current, request_cookie: event.target.value }))}
                      />
                    </label>
                    <div className="mt-2 text-xs text-slate-500">Cookie 预览：{previewCookie(form.request_cookie)}</div>
                    <label className="mt-4 block text-sm text-slate-300">
                      附加请求头 JSON
                      <textarea
                        className="mt-2 min-h-20 w-full rounded-2xl border border-white/10 bg-black/20 px-3 py-2 font-mono text-xs text-white outline-none focus:border-sky-300/50"
                        value={form.request_extra_headers_text}
                        onChange={(event) => setForm((current) => ({ ...current, request_extra_headers_text: event.target.value }))}
                      />
                    </label>
                    {form.request_method === "POST" && (
                      <label className="mt-4 block text-sm text-slate-300">
                        请求体
                        <textarea
                          className="mt-2 min-h-24 w-full rounded-2xl border border-white/10 bg-black/20 px-3 py-2 font-mono text-xs text-white outline-none focus:border-sky-300/50"
                          value={form.request_body_text}
                          onChange={(event) => setForm((current) => ({ ...current, request_body_text: event.target.value }))}
                        />
                      </label>
                    )}
                  </div>
                </div>

                <div className="grid gap-5 2xl:grid-cols-2">
                  <div className="apple-panel rounded-[24px] p-5">
                    <div className="text-sm font-medium text-slate-200">详情接口与匹配阈值</div>
                    <label className="mt-4 block text-sm text-slate-300">
                      详情 URL 模板
                      <input
                        className="mt-2 w-full rounded-2xl border border-white/10 bg-black/20 px-3 py-2 font-mono text-xs text-white outline-none focus:border-sky-300/50"
                        value={form.detail_url_template}
                        onChange={(event) => setForm((current) => ({ ...current, detail_url_template: event.target.value }))}
                      />
                    </label>
                    <div className="mt-4 grid gap-4 md:grid-cols-3">
                      <label className="block text-sm text-slate-300">
                        详情方法
                        <select
                          className="mt-2 w-full rounded-2xl border border-white/10 bg-black/20 px-3 py-2 text-sm text-white outline-none"
                          value={form.detail_request_method}
                          onChange={(event) =>
                            setForm((current) => ({ ...current, detail_request_method: normalizeMethod(event.target.value) }))
                          }
                        >
                          <option value="GET">GET</option>
                          <option value="POST">POST</option>
                        </select>
                      </label>
                      <label className="block text-sm text-slate-300">
                        高相似
                        <input
                          type="number"
                          step={0.01}
                          min={0}
                          max={1}
                          className="mt-2 w-full rounded-2xl border border-white/10 bg-black/20 px-3 py-2 text-sm text-white outline-none"
                          value={form.high_similarity_threshold}
                          onChange={(event) =>
                            setForm((current) => ({ ...current, high_similarity_threshold: Number(event.target.value) || 0.78 }))
                          }
                        />
                      </label>
                      <label className="block text-sm text-slate-300">
                        中相似
                        <input
                          type="number"
                          step={0.01}
                          min={0}
                          max={1}
                          className="mt-2 w-full rounded-2xl border border-white/10 bg-black/20 px-3 py-2 text-sm text-white outline-none"
                          value={form.medium_similarity_threshold}
                          onChange={(event) =>
                            setForm((current) => ({ ...current, medium_similarity_threshold: Number(event.target.value) || 0.55 }))
                          }
                        />
                      </label>
                    </div>
                    <label className="mt-4 block text-sm text-slate-300">
                      详情 Cookie
                      <textarea
                        className="mt-2 min-h-16 w-full rounded-2xl border border-white/10 bg-black/20 px-3 py-2 font-mono text-xs text-white outline-none"
                        value={form.detail_request_cookie}
                        onChange={(event) => setForm((current) => ({ ...current, detail_request_cookie: event.target.value }))}
                      />
                    </label>
                    <label className="mt-4 block text-sm text-slate-300">
                      详情附加请求头 JSON
                      <textarea
                        className="mt-2 min-h-20 w-full rounded-2xl border border-white/10 bg-black/20 px-3 py-2 font-mono text-xs text-white outline-none"
                        value={form.detail_request_extra_headers_text}
                        onChange={(event) =>
                          setForm((current) => ({ ...current, detail_request_extra_headers_text: event.target.value }))
                        }
                      />
                    </label>
                    {form.detail_request_method === "POST" && (
                      <label className="mt-4 block text-sm text-slate-300">
                        详情请求体
                        <textarea
                          className="mt-2 min-h-20 w-full rounded-2xl border border-white/10 bg-black/20 px-3 py-2 font-mono text-xs text-white outline-none"
                          value={form.detail_request_body_text}
                          onChange={(event) =>
                            setForm((current) => ({ ...current, detail_request_body_text: event.target.value }))
                          }
                        />
                      </label>
                    )}
                  </div>

                  <div className="apple-panel rounded-[24px] p-5">
                    <div className="text-sm font-medium text-slate-200">模型与接口预览</div>
                    <div className="mt-4">
                      <ModelSelector
                        value={form.model_config}
                        onChange={(model_config) => setForm((current) => ({ ...current, model_config }))}
                      />
                    </div>
                    <label className="mt-4 flex items-center gap-3 text-sm text-slate-300">
                      <input
                        type="checkbox"
                        checked={form.model_review_enabled}
                        onChange={(event) =>
                          setForm((current) => ({ ...current, model_review_enabled: event.target.checked }))
                        }
                      />
                      模型精判
                    </label>
                    <div className="mt-2 text-xs leading-6 text-slate-500">
                      开启后会对边界候选调用当前模型做二次判断，结果更保守，但运行会明显变慢。
                    </div>
                    {testResult && (
                      <div className="mt-5 rounded-2xl border border-white/10 bg-white/[0.03] p-4">
                        <div className="flex items-center justify-between gap-3">
                          <div className="text-sm font-medium text-slate-200">接口检查</div>
                          <span className={testResult.ok ? "text-sm text-emerald-300" : "text-sm text-rose-300"}>
                            HTTP {testResult.status_code}
                          </span>
                        </div>
                        <div className="mt-3 grid gap-3 text-xs text-slate-400 md:grid-cols-3">
                          <div>解析计数：{testResult.parsed_item_count}</div>
                          <div>工单预览：{testResult.parsed_issue_count}</div>
                          <div>类型：{testResult.response_content_type || "-"}</div>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </section>
            )}

            <section className="apple-panel rounded-[24px] p-5">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <div>
                  <div className="text-sm font-medium text-slate-200">当前查询面板工单</div>
                  <div className="mt-1 text-xs text-slate-500">
                    {selectedRun ? `${formatDate(selectedRun.started_at)} · 共 ${currentIssues.length} 条` : "暂无运行记录"}
                  </div>
                </div>
                {selectedRun && (
                  <select
                    className="rounded-2xl border border-white/10 bg-black/20 px-3 py-2 text-sm text-white outline-none"
                    value={selectedRun?.id ?? ""}
                    onChange={(event) => setSelectedRunId(event.target.value)}
                  >
                    {runs.map((run) => (
                      <option key={run.id} value={run.id}>
                        {formatDate(run.started_at)} · {run.status}
                      </option>
                    ))}
                  </select>
                )}
              </div>

              {currentIssues.length > 0 ? (
                <div className="mt-4 grid gap-3">
                  {sortedCurrentIssues.map((item) => (
                    <div key={item.issue_key} className="rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-sm text-sky-200">{item.issue_key}</span>
                        <span className={"rounded-full border px-2.5 py-1 text-xs " + matchBadge(item.match_level)}>
                          {matchLabel(item.match_level)} · {(item.match_score * 100).toFixed(0)}%
                        </span>
                        <span className="text-xs text-slate-500">{item.status || "-"}</span>
                      </div>
                      <div className="mt-2 break-words text-sm text-slate-200">{item.title || "未解析到标题"}</div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="mt-4 rounded-2xl border border-dashed border-white/10 p-8 text-center text-sm text-slate-500">
                  先保存配置并点击立即运行，当前查询到的 Jira 号和问题会显示在这里。
                </div>
              )}
            </section>

            <section className="apple-panel rounded-[24px] p-5">
              <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                <div>
                  <div className="text-sm font-medium text-slate-200">运行结果</div>
                  <div className="mt-1 text-xs text-slate-500">
                    {selectedRun ? selectedRun.summary : "暂无运行记录"}
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    className={
                      "rounded-full border px-3 py-2 text-sm " +
                      (activeResultGroup === "high"
                        ? "border-emerald-300/50 bg-emerald-400/10 text-emerald-100"
                        : "border-white/10 text-slate-300")
                    }
                    onClick={() => setActiveResultGroup("high")}
                  >
                    建议复用 {groupedResults.high.length}
                  </button>
                  <button
                    className={
                      "rounded-full border px-3 py-2 text-sm " +
                      (activeResultGroup === "medium"
                        ? "border-amber-300/50 bg-amber-400/10 text-amber-100"
                        : "border-white/10 text-slate-300")
                    }
                    onClick={() => setActiveResultGroup("medium")}
                  >
                    人工判断 {groupedResults.medium.length}
                  </button>
                  <button
                    className={
                      "rounded-full border px-3 py-2 text-sm " +
                      (activeResultGroup === "none"
                        ? "border-slate-300/40 bg-slate-400/10 text-slate-100"
                        : "border-white/10 text-slate-300")
                    }
                    onClick={() => setActiveResultGroup("none")}
                  >
                    未命中 {groupedResults.none.length}
                  </button>
                </div>
              </div>

              {selectedRun?.error_message && (
                <div className="mt-4 rounded-2xl border border-rose-400/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-100">
                  {selectedRun.error_message}
                </div>
              )}

              {visibleResults.length > 0 ? (
                <div className="mt-5 space-y-4">
                  {visibleResults.map((result) => (
                    <IssueResultCard key={result.issue_key} result={result} />
                  ))}
                </div>
              ) : (
                <div className="mt-4 rounded-2xl border border-dashed border-white/10 p-8 text-center text-sm text-slate-500">
                  当前分类下还没有卡片。
                </div>
              )}
            </section>
          </div>
        </main>
      </div>
    </div>
  );
}

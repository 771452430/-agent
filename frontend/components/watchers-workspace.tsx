"use client";

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
  WatcherRequestMethod,
  WatcherRun
} from "../lib/types";
import { ModelSelector } from "./model-selector";
import { useModelSettings } from "./model-settings-provider";

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
  poll_interval_minutes: number;
  recipient_emails_text: string;
  model_config: ModelConfig;
  enabled: boolean;
  owner_rules: OwnerRuleFormState[];
};

function formatDate(value?: string | null) {
  if (value == null || value === "") return "-";
  return new Date(value).toLocaleString("zh-CN");
}

function parseCommaList(value: string): string[] {
  return value
    .split(/[\n,，;]/)
    .map((item) => item.trim())
    .filter((item) => item !== "");
}

function stringifyJson(value: unknown) {
  return JSON.stringify(value, null, 2);
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
  return Object.fromEntries(
    Object.entries(headers).filter(([key]) => key.toLowerCase() !== "cookie")
  );
}

function normalizeMethod(value?: string | null): WatcherRequestMethod {
  return value?.toUpperCase() === "POST" ? "POST" : "GET";
}

function shellTokenize(command: string): string[] {
  const tokens: string[] = [];
  const normalized = command.replace(/\\\r?\n/g, " ");
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

  if (sawData && requestBodyText.trim() !== "") {
    JSON.parse(requestBodyText);
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
  };
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
    request_body_text: watcher.request_body_json ? stringifyJson(watcher.request_body_json) : "",
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

  if (assigneeCode === "" && services.length === 0 && modules.length === 0 && keywords.length === 0) {
    return null;
  }
  if (assigneeCode === "") {
    throw new Error("负责人规则里的经办人编码不能为空。");
  }
  return {
    assignee_code: assigneeCode,
    services,
    modules,
    keywords,
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
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [runningMode, setRunningMode] = useState<"snapshot" | "assign_current" | null>(null);
  const [isTesting, setIsTesting] = useState(false);
  const [togglingWatcherId, setTogglingWatcherId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<WatcherFetchTestResponse | null>(null);
  const modelValidation = validateModelConfig(form.model_config);

  const selectedRun = useMemo(
    () => runs.find((item) => item.id === selectedRunId) ?? runs[0] ?? null,
    [runs, selectedRunId]
  );
  const selectedWatcher = useMemo(
    () => watchers.find((item) => item.id === form.id) ?? null,
    [watchers, form.id]
  );

  async function loadWatcherDetail(watcherId: string) {
    const [watcher, watcherRuns] = await Promise.all([getWatcher(watcherId), listWatcherRuns(watcherId)]);
    setForm(watcherToForm(watcher));
    setRuns(watcherRuns);
    setSelectedRunId(watcherRuns[0]?.id ?? null);
    setTestResult(null);
  }

  async function bootstrap() {
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
  }

  useEffect(() => {
    bootstrap().catch((cause) => setError(String(cause)));
  }, []);

  async function refreshWatchers(selectId?: string) {
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

  function buildFetchRequest() {
    const requestHeaders =
      form.request_extra_headers_text.trim() === ""
        ? {}
        : (JSON.parse(form.request_extra_headers_text) as Record<string, string>);
    if (form.request_cookie.trim() !== "") {
      requestHeaders.Cookie = form.request_cookie.trim();
    }
    return {
      dashboard_url: form.dashboard_url.trim(),
      request_method: form.request_method,
      request_headers: requestHeaders,
      request_body_json:
        form.request_method === "POST"
          ? form.request_body_text.trim() === ""
            ? {}
            : (JSON.parse(form.request_body_text) as Record<string, unknown>)
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

  function buildPayload() {
    return {
      name: form.name.trim(),
      description: form.description.trim(),
      ...buildFetchRequest(),
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
    if (!modelValidation.isRunnable) {
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
    if (!modelValidation.isRunnable) {
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
    if (form.dashboard_url.trim() === "") {
      setError("请先填写面板 URL。");
      return;
    }

    setError("");
    setIsTesting(true);
    try {
      const result = await testWatcherFetch(buildFetchRequest());
      setTestResult(result);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setIsTesting(false);
    }
  }

  async function handleToggleWatcherEnabled(watcher: WatcherAgentConfig, nextEnabled: boolean) {
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
    <div className="grid h-screen grid-cols-[320px_minmax(0,1fr)_420px] overflow-hidden">
      <aside className="min-h-0 overflow-y-auto border-r border-slate-800 bg-slate-900/50 p-5">
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
            }}
          >
            新建
          </button>
        </div>

        <div className="mt-5 space-y-3">
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
                void loadWatcherDetail(watcher.id);
              }}
              onKeyDown={(event) => {
                if (event.key !== "Enter" && event.key !== " ") return;
                event.preventDefault();
                void loadWatcherDetail(watcher.id);
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
        </div>
      </aside>

      <main className="min-h-0 min-w-0 overflow-y-auto border-r border-slate-800 px-6 py-6">
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
                disabled={runningMode != null}
              >
                {runningMode === "snapshot" ? "运行中..." : "立即运行"}
              </button>
              <button
                className="rounded-xl border border-amber-500/50 px-4 py-2 text-sm text-amber-200 hover:border-amber-400 disabled:opacity-50"
                onClick={() => {
                  void handleRun("assign_current");
                }}
                disabled={runningMode != null}
              >
                {runningMode === "assign_current" ? "分配中..." : "立即运行并分配当前列表"}
              </button>
              <button
                className="rounded-xl border border-sky-500/50 px-4 py-2 text-sm text-sky-200 hover:border-sky-400 disabled:opacity-50"
                onClick={() => {
                  void handleTestFetch();
                }}
                disabled={isTesting}
              >
                {isTesting ? "检查中..." : "接口检查"}
              </button>
              <button
                className="rounded-xl bg-amber-300 px-4 py-2 text-sm font-medium text-slate-950 hover:bg-amber-200 disabled:bg-slate-700"
                onClick={() => {
                  void handleSave();
                }}
                disabled={isSaving}
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
                    支持自动回填 URL、方法、Cookie、附加请求头、请求体 JSON。
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
                <span className="text-slate-400">请求体 JSON</span>
                <textarea
                  className="min-h-40 rounded-2xl border border-slate-700 bg-slate-950 px-3 py-3 font-mono text-xs leading-6"
                  value={form.request_body_text}
                  onChange={(event) => setForm((current) => ({ ...current, request_body_text: event.target.value }))}
                  placeholder='{"pageNumber":1,"pageSize":30}'
                />
              </label>
            )}
            <div className="lg:col-span-2">
              <ModelSelector
                value={form.model_config}
                onChange={(nextModelConfig) => setForm((current) => ({ ...current, model_config: nextModelConfig }))}
              />
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
                <div className="mt-1 text-xs text-slate-500">规则优先，未命中时才进入 LLM 兜底分配。</div>
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
                      <span className="text-slate-400">经办人编码</span>
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
                        placeholder="0000140558"
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
                      命中规则后，会自动调用 PM 的 `PUT /defect/update`，使用当前 Bug 的 `aid` 和这里填写的经办人编码完成分配。
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {error !== "" && <div className="mt-6 text-sm text-rose-300 whitespace-pre-wrap">{error}</div>}
      </main>

      <aside className="min-h-0 overflow-y-auto bg-slate-900/60 p-5">
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
                          PM aid：{bug.bug_aid || "-"} · 服务模块：{bug.service || "-"} / {bug.module || "-"} · 状态：{bug.status || "-"} · 经办人：{bug.assignee || "-"}
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
                    {testResult.request_body_json ? stringifyJson(testResult.request_body_json) : "(空请求体)"}
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
                        PM aid：{item.bug_aid || "-"} · 经办人编码：{item.assignee_code || "未匹配"}
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

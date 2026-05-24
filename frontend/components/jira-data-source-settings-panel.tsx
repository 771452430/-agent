"use client";

import { useEffect, useMemo, useState } from "react";

import { useModelSettings } from "./model-settings-provider";

type Draft = {
  enabled: boolean;
  db_path: string;
  app_key: string;
  app_secret: string;
  sync_keyword: string;
  sync_date_range: string;
  sync_interval_minutes: string;
};

function buildDraft(settings: ReturnType<typeof useModelSettings>["jiraDataSourceSettings"]): Draft {
  return {
    enabled: settings?.enabled ?? false,
    db_path: settings?.db_path || "backend/data/jira/jira_support.db",
    app_key: settings?.app_key || "",
    app_secret: "",
    sync_keyword: settings?.sync_keyword || "工作台",
    sync_date_range: settings?.sync_date_range || "本年",
    sync_interval_minutes: String(settings?.sync_interval_minutes || 1440)
  };
}

function credentialSourceLabel(source: string | undefined) {
  if (source === "settings") return "页面配置";
  if (source === "environment") return "环境变量";
  if (source === "legacy_skill") return "旧 Skill 配置";
  return "";
}

export function JiraDataSourceSettingsPanel() {
  const {
    jiraDataSourceSettings,
    jiraDataSourceRuns,
    isJiraDataSourceSettingsLoading,
    jiraDataSourceError,
    isJiraDataSourceSettingsOpen,
    closeJiraDataSourceSettings,
    saveJiraDataSourceSettings,
    runJiraDataSourceTest,
    runJiraDataSourceSync
  } = useModelSettings();
  const [draft, setDraft] = useState<Draft>(() => buildDraft(jiraDataSourceSettings));
  const [status, setStatus] = useState<{ tone: "neutral" | "success" | "error"; message: string }>({
    tone: "neutral",
    message: ""
  });
  const [isSaving, setIsSaving] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);

  useEffect(() => {
    if (!isJiraDataSourceSettingsOpen) return;
    setDraft(buildDraft(jiraDataSourceSettings));
    setStatus({ tone: "neutral", message: "" });
  }, [isJiraDataSourceSettingsOpen, jiraDataSourceSettings]);

  const statusClassName = useMemo(() => {
    if (status.tone === "success") return "text-emerald-300";
    if (status.tone === "error") return "text-rose-300";
    return "text-slate-400";
  }, [status.tone]);

  async function handleSave() {
    setIsSaving(true);
    setStatus({ tone: "neutral", message: "正在保存 Jira 数据源配置..." });
    try {
      const saved = await saveJiraDataSourceSettings({
        enabled: draft.enabled,
        db_path: draft.db_path.trim(),
        app_key: draft.app_key.trim(),
        app_secret: draft.app_secret.trim(),
        sync_keyword: draft.sync_keyword.trim(),
        sync_date_range: draft.sync_date_range.trim(),
        sync_interval_minutes: Number(draft.sync_interval_minutes || 1440)
      });
      setDraft(buildDraft(saved));
      setStatus({ tone: "success", message: "Jira 数据源配置已保存。" });
    } catch (cause) {
      setStatus({ tone: "error", message: String(cause) });
    } finally {
      setIsSaving(false);
    }
  }

  async function handleTest() {
    setIsTesting(true);
    setStatus({ tone: "neutral", message: "正在测试同步关键字..." });
    try {
      const result = await runJiraDataSourceTest();
      setStatus({
        tone: result.ok ? "success" : "error",
        message:
          result.message +
          (result.matched_preview.length > 0 ? " 示例：" + result.matched_preview.slice(0, 4).join("；") : "")
      });
    } catch (cause) {
      setStatus({ tone: "error", message: String(cause) });
    } finally {
      setIsTesting(false);
    }
  }

  async function handleSync() {
    setIsSyncing(true);
    setStatus({ tone: "neutral", message: "正在同步 Jira 数据，时间取决于匹配模块数量..." });
    try {
      const run = await runJiraDataSourceSync();
      setStatus({
        tone: run.status === "success" ? "success" : "error",
        message: run.error_message || run.summary
      });
    } catch (cause) {
      setStatus({ tone: "error", message: String(cause) });
    } finally {
      setIsSyncing(false);
    }
  }

  if (!isJiraDataSourceSettingsOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/75 p-6 backdrop-blur-sm">
      <div className="h-[min(780px,94vh)] w-[min(940px,96vw)] overflow-hidden rounded-[32px] border border-slate-800 bg-slate-950 shadow-2xl shadow-black/40">
        <section className="h-full overflow-y-auto bg-[radial-gradient(circle_at_top_right,rgba(34,197,94,0.1),transparent_32%),linear-gradient(180deg,rgba(15,23,42,0.98),rgba(2,6,23,1))] px-8 py-7">
          <div className="flex items-start justify-between gap-6">
            <div>
              <div className="text-sm text-slate-400">Jira 数据源设置</div>
              <h3 className="mt-2 text-3xl font-semibold text-slate-50">同步历史支持问题库</h3>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-400">
                同步后的 `jira_support.db` 会作为 Jira 工单 Agent 和 Jira 方案检索 Agent 的本地历史库。
                密钥只保存在后端，页面不会回显明文。
              </p>
            </div>
            <button
              className="rounded-2xl border border-slate-700 px-4 py-2 text-sm text-slate-300 transition hover:border-slate-500"
              onClick={closeJiraDataSourceSettings}
            >
              关闭
            </button>
          </div>

          {jiraDataSourceError !== "" && (
            <div className="mt-5 rounded-2xl border border-rose-400/30 bg-rose-400/10 px-4 py-3 text-sm text-rose-300">
              {jiraDataSourceError}
            </div>
          )}
          {isJiraDataSourceSettingsLoading && <div className="mt-5 text-sm text-slate-400">正在加载 Jira 数据源配置...</div>}

          <div className="mt-8 grid gap-5">
            <section className="rounded-[28px] border border-slate-800 bg-slate-900/80 p-5">
              <div className="rounded-2xl border border-slate-800 bg-slate-950/60 px-4 py-3 text-sm text-slate-400">
                当前状态：
                {jiraDataSourceSettings?.has_app_secret ? (
                  <span className="ml-2 text-emerald-300">
                    密钥已配置
                    {credentialSourceLabel(jiraDataSourceSettings.credential_source)
                      ? "（" + credentialSourceLabel(jiraDataSourceSettings.credential_source) + "）"
                      : ""}
                  </span>
                ) : (
                  <span className="ml-2 text-amber-300">缺少 AppSecret</span>
                )}
                {jiraDataSourceSettings?.last_sync_status ? (
                  <span className="ml-2 text-cyan-300">最近同步：{jiraDataSourceSettings.last_sync_status}</span>
                ) : (
                  <span className="ml-2 text-slate-500">尚未同步</span>
                )}
              </div>

              <div className="mt-5 grid gap-5 md:grid-cols-2">
                <label className="grid gap-2 text-sm md:col-span-2">
                  <span className="text-slate-400">服务器 DB 路径</span>
                  <input
                    className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none placeholder:text-slate-600"
                    value={draft.db_path}
                    onChange={(event) => setDraft((current) => ({ ...current, db_path: event.target.value }))}
                  />
                </label>
                <label className="grid gap-2 text-sm">
                  <span className="text-slate-400">AppKey</span>
                  <input
                    className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none placeholder:text-slate-600"
                    value={draft.app_key}
                    onChange={(event) => setDraft((current) => ({ ...current, app_key: event.target.value }))}
                  />
                </label>
                <label className="grid gap-2 text-sm">
                  <span className="text-slate-400">AppSecret</span>
                  <input
                    type="password"
                    className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none placeholder:text-slate-600"
                    value={draft.app_secret}
                    onChange={(event) => setDraft((current) => ({ ...current, app_secret: event.target.value }))}
                    placeholder={jiraDataSourceSettings?.app_secret_masked || "粘贴 Jira AppSecret"}
                  />
                </label>
                <label className="grid gap-2 text-sm">
                  <span className="text-slate-400">同步关键字</span>
                  <input
                    className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none placeholder:text-slate-600"
                    value={draft.sync_keyword}
                    onChange={(event) => setDraft((current) => ({ ...current, sync_keyword: event.target.value }))}
                    placeholder="例如：工作台 / 流程中心 / 公式"
                  />
                </label>
                <label className="grid gap-2 text-sm">
                  <span className="text-slate-400">时间范围</span>
                  <input
                    className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none placeholder:text-slate-600"
                    value={draft.sync_date_range}
                    onChange={(event) => setDraft((current) => ({ ...current, sync_date_range: event.target.value }))}
                    placeholder="本年 / 本月 / 最近三天 / 2026年"
                  />
                </label>
                <label className="grid gap-2 text-sm">
                  <span className="text-slate-400">定时周期（分钟）</span>
                  <input
                    type="number"
                    min={1}
                    max={1440}
                    className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none placeholder:text-slate-600"
                    value={draft.sync_interval_minutes}
                    onChange={(event) => setDraft((current) => ({ ...current, sync_interval_minutes: event.target.value }))}
                  />
                </label>
                <label className="flex items-center gap-3 rounded-2xl border border-slate-800 bg-slate-950/60 px-4 py-3 text-sm text-slate-300">
                  <input
                    type="checkbox"
                    checked={draft.enabled}
                    onChange={(event) => setDraft((current) => ({ ...current, enabled: event.target.checked }))}
                  />
                  开启定时同步
                </label>
              </div>
            </section>

            <section className="rounded-[28px] border border-slate-800 bg-slate-900/80 p-5 text-sm text-slate-400">
              <div className="font-medium text-slate-100">最近同步</div>
              <div className="mt-3 space-y-2">
                {jiraDataSourceRuns.length === 0 ? (
                  <div>暂无同步记录。</div>
                ) : (
                  jiraDataSourceRuns.slice(0, 5).map((run) => (
                    <div key={run.id} className="rounded-2xl border border-slate-800 bg-slate-950/60 px-4 py-3">
                      <div className="flex flex-wrap items-center gap-2 text-slate-200">
                        <span>{run.status}</span>
                        <span>{run.keyword}</span>
                        <span>{run.date_range}</span>
                        <span>{run.fetched_count} 条</span>
                      </div>
                      <div className="mt-1 text-slate-500">{run.error_message || run.summary}</div>
                    </div>
                  ))
                )}
              </div>
            </section>

            <div className="flex flex-wrap justify-end gap-3">
              <button
                className="rounded-2xl border border-slate-700 px-4 py-3 text-sm text-slate-300 transition hover:border-slate-500 disabled:opacity-60"
                disabled={isSaving || isTesting || isSyncing}
                onClick={() => void handleSave()}
              >
                {isSaving ? "保存中..." : "保存配置"}
              </button>
              <button
                className="rounded-2xl border border-cyan-300/40 px-4 py-3 text-sm text-cyan-200 transition hover:border-cyan-200 disabled:opacity-60"
                disabled={isSaving || isTesting || isSyncing}
                onClick={() => void handleTest()}
              >
                {isTesting ? "测试中..." : "测试匹配"}
              </button>
              <button
                className="rounded-2xl bg-emerald-300 px-4 py-3 text-sm font-medium text-slate-950 transition hover:bg-emerald-200 disabled:opacity-60"
                disabled={isSaving || isTesting || isSyncing}
                onClick={() => void handleSync()}
              >
                {isSyncing ? "同步中..." : "立即同步"}
              </button>
            </div>

            <div className={"text-sm " + statusClassName}>{status.message}</div>
          </div>
        </section>
      </div>
    </div>
  );
}

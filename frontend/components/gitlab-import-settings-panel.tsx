"use client";

import { useEffect, useMemo, useState } from "react";

import { useModelSettings } from "./model-settings-provider";

type GitLabDraft = {
  token: string;
  allowedHostsText: string;
};

function buildDraft(settings: ReturnType<typeof useModelSettings>["gitlabImportSettings"]): GitLabDraft {
  return {
    token: "",
    allowedHostsText: (settings?.allowed_hosts ?? []).join("\n")
  };
}

function parseHosts(rawValue: string): string[] {
  return rawValue
    .split(/\r?\n|,/)
    .map((item) => item.trim().toLowerCase())
    .filter((item) => item !== "");
}

export function GitLabImportSettingsPanel() {
  const {
    gitlabImportSettings,
    gitlabImportError,
    isGitLabImportSettingsLoading,
    isGitLabImportSettingsOpen,
    closeGitLabImportSettings,
    saveGitLabImportSettings
  } = useModelSettings();
  const [draft, setDraft] = useState<GitLabDraft>(buildDraft(null));
  const [status, setStatus] = useState<{ tone: "neutral" | "success" | "error"; message: string }>({
    tone: "neutral",
    message: ""
  });
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    if (!isGitLabImportSettingsOpen) return;
    setDraft(buildDraft(gitlabImportSettings));
    setStatus({ tone: "neutral", message: "" });
  }, [isGitLabImportSettingsOpen, gitlabImportSettings]);

  const statusClassName = useMemo(() => {
    if (status.tone === "success") return "text-emerald-300";
    if (status.tone === "error") return "text-rose-300";
    return "text-slate-400";
  }, [status.tone]);

  async function handleSave(options?: { clearToken?: boolean }) {
    setIsSaving(true);
    setStatus({ tone: "neutral", message: "正在保存 GitLab 导入配置..." });
    try {
      const saved = await saveGitLabImportSettings({
        ...(draft.token.trim() !== "" ? { token: draft.token.trim() } : {}),
        ...(options?.clearToken ? { clear_token: true } : {}),
        allowed_hosts: parseHosts(draft.allowedHostsText)
      });
      setDraft(buildDraft(saved));
      setStatus({ tone: "success", message: options?.clearToken ? "已清空 GitLab Token。" : "GitLab 导入配置已保存。" });
    } catch (cause) {
      setStatus({ tone: "error", message: String(cause) });
    } finally {
      setIsSaving(false);
    }
  }

  if (!isGitLabImportSettingsOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/75 p-6 backdrop-blur-sm">
      <div className="h-[min(760px,94vh)] w-[min(920px,96vw)] overflow-hidden rounded-[32px] border border-slate-800 bg-slate-950 shadow-2xl shadow-black/40">
        <section className="h-full overflow-y-auto bg-[radial-gradient(circle_at_top_right,rgba(34,197,94,0.08),transparent_32%),linear-gradient(180deg,rgba(15,23,42,0.98),rgba(2,6,23,1))] px-8 py-7">
          <div className="flex items-start justify-between gap-6">
            <div>
              <div className="text-sm text-slate-400">GitLab 导入设置</div>
              <h3 className="mt-2 text-3xl font-semibold text-slate-50">GitLab 文档树导入凭据</h3>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-400">
                检索模式里的 GitLab 文档树导入会统一复用这里的 `Personal Access Token` 和域名白名单。
                页面不会回显 Token 明文；若这里未配置，会继续回退到服务端环境变量。
              </p>
            </div>
            <button
              className="rounded-2xl border border-slate-700 px-4 py-2 text-sm text-slate-300 transition hover:border-slate-500"
              onClick={closeGitLabImportSettings}
            >
              关闭
            </button>
          </div>

          {gitlabImportError !== "" && (
            <div className="mt-5 rounded-2xl border border-rose-400/30 bg-rose-400/10 px-4 py-3 text-sm text-rose-300">
              {gitlabImportError}
            </div>
          )}
          {isGitLabImportSettingsLoading && <div className="mt-5 text-sm text-slate-400">正在加载 GitLab 导入配置...</div>}

          <div className="mt-8 space-y-5">
            <section className="rounded-[28px] border border-slate-800 bg-slate-900/80 p-5">
              <div className="grid gap-5">
                <div className="rounded-2xl border border-slate-800 bg-slate-950/60 px-4 py-3 text-sm text-slate-400">
                  当前状态：
                  {gitlabImportSettings?.configured ? (
                    <span className="ml-2 text-emerald-300">
                      已配置 Token（来源：{gitlabImportSettings.token_source === "database" ? "设置页" : "环境变量"}）
                    </span>
                  ) : (
                    <span className="ml-2 text-slate-500">未完成配置</span>
                  )}
                </div>

                <label className="grid gap-2 text-sm">
                  <span className="text-slate-400">Personal Access Token</span>
                  <input
                    type="password"
                    className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none placeholder:text-slate-600"
                    value={draft.token}
                    onChange={(event) => setDraft((current) => ({ ...current, token: event.target.value }))}
                    placeholder={gitlabImportSettings?.token_masked || "粘贴 GitLab Personal Access Token；留空表示保留当前值"}
                  />
                </label>

                <label className="grid gap-2 text-sm">
                  <span className="text-slate-400">允许导入的 GitLab 域名</span>
                  <textarea
                    className="min-h-32 rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none placeholder:text-slate-600"
                    value={draft.allowedHostsText}
                    onChange={(event) => setDraft((current) => ({ ...current, allowedHostsText: event.target.value }))}
                    placeholder={"git.yyrd.com\n一行一个域名，留空会回退到环境变量或默认值"}
                  />
                </label>
              </div>
            </section>

            <section className="rounded-[28px] border border-slate-800 bg-slate-900/80 p-5 text-sm leading-7 text-slate-400">
              <div className="font-medium text-slate-100">使用说明</div>
              <div className="mt-2">- 这里填写的是 GitLab 的个人访问令牌（Personal Access Token），不是 SSH Key。</div>
              <div>- 建议至少给 `read_api` 权限；如果企业 GitLab 策略更严格，再由管理员补充权限。</div>
              <div>- 域名白名单用于限制可导入的 GitLab 站点，默认只开放 `git.yyrd.com`。</div>
              <div>- 检索模式导入成功后，支持问题 Agent 只要把知识范围指向对应节点，就能复用这些文档。</div>
            </section>

            <div className="flex items-center justify-between gap-3">
              <button
                className="rounded-2xl border border-rose-500/40 px-4 py-3 text-sm text-rose-200 transition hover:border-rose-400 disabled:opacity-60"
                onClick={() => {
                  void handleSave({ clearToken: true });
                }}
                disabled={isSaving || !gitlabImportSettings?.has_token}
              >
                清空已保存 Token
              </button>
              <button
                className="rounded-2xl bg-emerald-300 px-4 py-3 text-sm font-medium text-slate-950 transition hover:bg-emerald-200 disabled:opacity-60"
                onClick={() => {
                  void handleSave();
                }}
                disabled={isSaving}
              >
                {isSaving ? "保存中..." : "保存配置"}
              </button>
            </div>

            <div className={"text-sm " + statusClassName}>{status.message}</div>
          </div>
        </section>
      </div>
    </div>
  );
}

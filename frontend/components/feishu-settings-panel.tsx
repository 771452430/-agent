"use client";

import { useEffect, useMemo, useState } from "react";

import { useModelSettings } from "./model-settings-provider";

type FeishuDraft = {
  app_id: string;
  app_secret: string;
};

function buildDraft(): FeishuDraft {
  return {
    app_id: "",
    app_secret: ""
  };
}

export function FeishuSettingsPanel() {
  const {
    feishuSettings,
    feishuError,
    isFeishuSettingsLoading,
    isFeishuSettingsOpen,
    closeFeishuSettings,
    saveFeishuSettings
  } = useModelSettings();
  const [draft, setDraft] = useState<FeishuDraft>(buildDraft());
  const [status, setStatus] = useState<{ tone: "neutral" | "success" | "error"; message: string }>({
    tone: "neutral",
    message: ""
  });
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    if (!isFeishuSettingsOpen) return;
    setDraft(buildDraft());
    setStatus({ tone: "neutral", message: "" });
  }, [isFeishuSettingsOpen]);

  const statusClassName = useMemo(() => {
    if (status.tone === "success") return "text-emerald-300";
    if (status.tone === "error") return "text-rose-300";
    return "text-slate-400";
  }, [status.tone]);

  async function handleSave() {
    setIsSaving(true);
    setStatus({ tone: "neutral", message: "正在保存飞书应用配置..." });
    try {
      await saveFeishuSettings({
        app_id: draft.app_id.trim(),
        app_secret: draft.app_secret.trim()
      });
      setDraft(buildDraft());
      setStatus({ tone: "success", message: "飞书应用配置已保存。" });
    } catch (cause) {
      setStatus({ tone: "error", message: String(cause) });
    } finally {
      setIsSaving(false);
    }
  }

  if (!isFeishuSettingsOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/75 p-6 backdrop-blur-sm">
      <div className="h-[min(720px,94vh)] w-[min(880px,96vw)] overflow-hidden rounded-[32px] border border-slate-800 bg-slate-950 shadow-2xl shadow-black/40">
        <section className="h-full overflow-y-auto bg-[radial-gradient(circle_at_top_right,rgba(96,165,250,0.08),transparent_32%),linear-gradient(180deg,rgba(15,23,42,0.98),rgba(2,6,23,1))] px-8 py-7">
          <div className="flex items-start justify-between gap-6">
            <div>
              <div className="text-sm text-slate-400">飞书设置</div>
              <h3 className="mt-2 text-3xl font-semibold text-slate-50">飞书自建应用配置</h3>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-400">
                支持问题 Agent 使用企业自建应用的 `App ID / App Secret` 自动换取 `tenant_access_token`
                访问多维表格。这里只保存服务端运行所需的应用凭据，页面不会回显密钥明文。
              </p>
            </div>
            <button
              className="rounded-2xl border border-slate-700 px-4 py-2 text-sm text-slate-300 transition hover:border-slate-500"
              onClick={closeFeishuSettings}
            >
              关闭
            </button>
          </div>

          {feishuError !== "" && (
            <div className="mt-5 rounded-2xl border border-rose-400/30 bg-rose-400/10 px-4 py-3 text-sm text-rose-300">
              {feishuError}
            </div>
          )}
          {isFeishuSettingsLoading && <div className="mt-5 text-sm text-slate-400">正在加载飞书配置...</div>}

          <div className="mt-8 space-y-5">
            <section className="rounded-[28px] border border-slate-800 bg-slate-900/80 p-5">
              <div className="grid gap-5">
                <div className="rounded-2xl border border-slate-800 bg-slate-950/60 px-4 py-3 text-sm text-slate-400">
                  当前状态：
                  {feishuSettings?.configured ? (
                    <span className="ml-2 text-emerald-300">已配置应用（{feishuSettings.app_id || "未填写 App ID"}）</span>
                  ) : (
                    <span className="ml-2 text-slate-500">未完成配置</span>
                  )}
                </div>

                <label className="grid gap-2 text-sm">
                  <span className="text-slate-400">App ID</span>
                  <input
                    type="text"
                    className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none placeholder:text-slate-600"
                    value={draft.app_id}
                    onChange={(event) => setDraft((current) => ({ ...current, app_id: event.target.value }))}
                    placeholder={feishuSettings?.app_id || "粘贴企业自建应用的 App ID"}
                  />
                </label>

                <label className="grid gap-2 text-sm">
                  <span className="text-slate-400">App Secret</span>
                  <input
                    type="password"
                    className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none placeholder:text-slate-600"
                    value={draft.app_secret}
                    onChange={(event) => setDraft((current) => ({ ...current, app_secret: event.target.value }))}
                    placeholder={feishuSettings?.app_secret_masked || "粘贴企业自建应用的 App Secret"}
                  />
                </label>
              </div>
            </section>

            <section className="rounded-[28px] border border-slate-800 bg-slate-900/80 p-5 text-sm leading-7 text-slate-400">
              <div className="font-medium text-slate-100">使用说明</div>
              <div className="mt-2">- 这里填写的是企业自建应用的 `App ID / App Secret`，服务端会自动换取 `tenant_access_token`。</div>
              <div>- 这里不能填写多维表格链接里的 `Base Token`。`OR...`、`tbl...`、`vew...` 都不是认证凭据。</div>
              <div>- 表格的 `App Token / Base Token` 与 `Table ID` 在每个支持问题 Agent 内单独配置。</div>
              <div>- 使用前请先在飞书开放平台创建企业自建应用、开通多维表格读写权限并发布版本。</div>
            </section>

            <div className="flex justify-end">
              <button
                className="rounded-2xl bg-amber-300 px-4 py-3 text-sm font-medium text-slate-950 transition hover:bg-amber-200 disabled:opacity-60"
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

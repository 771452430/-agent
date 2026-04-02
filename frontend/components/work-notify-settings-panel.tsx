"use client";

import { useEffect, useMemo, useState } from "react";

import { useModelSettings } from "./model-settings-provider";

type WorkNotifyDraft = {
  app_key: string;
  app_secret: string;
};

function buildDraft(): WorkNotifyDraft {
  return {
    app_key: "",
    app_secret: ""
  };
}

export function WorkNotifySettingsPanel() {
  const {
    workNotifySettings,
    workNotifyError,
    isWorkNotifySettingsLoading,
    isWorkNotifySettingsOpen,
    closeWorkNotifySettings,
    saveWorkNotifySettings
  } = useModelSettings();
  const [draft, setDraft] = useState<WorkNotifyDraft>(buildDraft());
  const [status, setStatus] = useState<{ tone: "neutral" | "success" | "error"; message: string }>({
    tone: "neutral",
    message: ""
  });
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    if (!isWorkNotifySettingsOpen) return;
    setDraft(buildDraft());
    setStatus({ tone: "neutral", message: "" });
  }, [isWorkNotifySettingsOpen]);

  const statusClassName = useMemo(() => {
    if (status.tone === "success") return "text-emerald-300";
    if (status.tone === "error") return "text-rose-300";
    return "text-slate-400";
  }, [status.tone]);

  async function handleSave() {
    setIsSaving(true);
    setStatus({ tone: "neutral", message: "正在保存工作通知配置..." });
    try {
      await saveWorkNotifySettings({
        app_key: draft.app_key.trim(),
        app_secret: draft.app_secret.trim()
      });
      setDraft(buildDraft());
      setStatus({ tone: "success", message: "工作通知配置已保存。" });
    } catch (cause) {
      setStatus({ tone: "error", message: String(cause) });
    } finally {
      setIsSaving(false);
    }
  }

  if (!isWorkNotifySettingsOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/75 p-6 backdrop-blur-sm">
      <div className="h-[min(720px,94vh)] w-[min(880px,96vw)] overflow-hidden rounded-[32px] border border-slate-800 bg-slate-950 shadow-2xl shadow-black/40">
        <section className="h-full overflow-y-auto bg-[radial-gradient(circle_at_top_right,rgba(14,165,233,0.1),transparent_32%),linear-gradient(180deg,rgba(15,23,42,0.98),rgba(2,6,23,1))] px-8 py-7">
          <div className="flex items-start justify-between gap-6">
            <div>
              <div className="text-sm text-slate-400">工作通知设置</div>
              <h3 className="mt-2 text-3xl font-semibold text-slate-50">用友工作通知凭据</h3>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-400">
                这里统一保存 `AppKey / AppSecret`。支持问题 Agent 的人工通知，以及内置
                `send_yonyou_work_notify` 工具，都会优先复用这份配置。
              </p>
            </div>
            <button
              className="rounded-2xl border border-slate-700 px-4 py-2 text-sm text-slate-300 transition hover:border-slate-500"
              onClick={closeWorkNotifySettings}
            >
              关闭
            </button>
          </div>

          {workNotifyError !== "" && (
            <div className="mt-5 rounded-2xl border border-rose-400/30 bg-rose-400/10 px-4 py-3 text-sm text-rose-300">
              {workNotifyError}
            </div>
          )}
          {isWorkNotifySettingsLoading && <div className="mt-5 text-sm text-slate-400">正在加载工作通知配置...</div>}

          <div className="mt-8 space-y-5">
            <section className="rounded-[28px] border border-slate-800 bg-slate-900/80 p-5">
              <div className="grid gap-5">
                <div className="rounded-2xl border border-slate-800 bg-slate-950/60 px-4 py-3 text-sm text-slate-400">
                  当前状态：
                  {workNotifySettings?.configured ? (
                    <span className="ml-2 text-emerald-300">已配置应用（{workNotifySettings.app_key || "未填写 AppKey"}）</span>
                  ) : (
                    <span className="ml-2 text-slate-500">未完成配置</span>
                  )}
                </div>

                <label className="grid gap-2 text-sm">
                  <span className="text-slate-400">AppKey</span>
                  <input
                    type="text"
                    className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none placeholder:text-slate-600"
                    value={draft.app_key}
                    onChange={(event) => setDraft((current) => ({ ...current, app_key: event.target.value }))}
                    placeholder={workNotifySettings?.app_key || "粘贴工作通知应用的 AppKey"}
                  />
                </label>

                <label className="grid gap-2 text-sm">
                  <span className="text-slate-400">AppSecret</span>
                  <input
                    type="password"
                    className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none placeholder:text-slate-600"
                    value={draft.app_secret}
                    onChange={(event) => setDraft((current) => ({ ...current, app_secret: event.target.value }))}
                    placeholder={workNotifySettings?.app_secret_masked || "粘贴工作通知应用的 AppSecret"}
                  />
                </label>
              </div>
            </section>

            <section className="rounded-[28px] border border-slate-800 bg-slate-900/80 p-5 text-sm leading-7 text-slate-400">
              <div className="font-medium text-slate-100">使用说明</div>
              <div className="mt-2">- 这份配置会被支持问题 Agent 的人工确认通知直接复用。</div>
              <div>- 内置 `send_yonyou_work_notify` 工具在未显式传 `app_key / app_secret` 时，也会优先读取这里。</div>
              <div>- `yhtUserId` 仍然属于业务接收人，不在全局设置里保存。</div>
              <div>- OpenAPI 域名和鉴权域名继续按现有环境变量或工具入参决定，不在这个面板里维护。</div>
            </section>

            <div className="flex justify-end">
              <button
                className="rounded-2xl bg-cyan-300 px-4 py-3 text-sm font-medium text-slate-950 transition hover:bg-cyan-200 disabled:opacity-60"
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

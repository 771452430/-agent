"use client";

import { useEffect, useMemo, useState } from "react";

import { useModelSettings } from "./model-settings-provider";

type MailDraft = {
  enabled: boolean;
  smtp_host: string;
  smtp_port: number;
  smtp_username: string;
  smtp_password: string;
  use_tls: boolean;
  use_ssl: boolean;
};

function buildDraft(mailSettings: ReturnType<typeof useModelSettings>["mailSettings"]): MailDraft | null {
  if (mailSettings == null) return null;
  return {
    enabled: mailSettings.enabled,
    smtp_host: mailSettings.smtp_host,
    smtp_port: mailSettings.smtp_port,
    smtp_username: mailSettings.smtp_username,
    smtp_password: "",
    use_tls: mailSettings.use_tls,
    use_ssl: mailSettings.use_ssl
  };
}

export function MailSettingsPanel() {
  const {
    mailSettings,
    mailError,
    isMailSettingsLoading,
    isMailSettingsOpen,
    closeMailSettings,
    saveMailSettings,
    runMailTest
  } = useModelSettings();
  const [draft, setDraft] = useState<MailDraft | null>(null);
  const [testRecipient, setTestRecipient] = useState("");
  const [status, setStatus] = useState<{ tone: "neutral" | "success" | "error"; message: string }>({
    tone: "neutral",
    message: ""
  });
  const [isSaving, setIsSaving] = useState(false);
  const [isTesting, setIsTesting] = useState(false);

  useEffect(() => {
    if (!isMailSettingsOpen) return;
    setDraft(buildDraft(mailSettings));
    setTestRecipient("");
    setStatus({ tone: "neutral", message: "" });
  }, [isMailSettingsOpen, mailSettings]);

  const statusClassName = useMemo(() => {
    if (status.tone === "success") return "text-emerald-300";
    if (status.tone === "error") return "text-rose-300";
    return "text-slate-400";
  }, [status.tone]);

  async function persistDraft() {
    if (draft == null) return null;
    return saveMailSettings({
      enabled: draft.enabled,
      smtp_host: draft.smtp_host,
      smtp_port: Number(draft.smtp_port),
      smtp_username: draft.smtp_username,
      use_tls: draft.use_tls,
      use_ssl: draft.use_ssl,
      ...(draft.smtp_password.trim() !== "" ? { smtp_password: draft.smtp_password.trim() } : {})
    });
  }

  async function handleSave() {
    if (draft == null) return;
    setIsSaving(true);
    setStatus({ tone: "neutral", message: "正在保存邮箱配置..." });
    try {
      const saved = await persistDraft();
      setDraft(buildDraft(saved));
      setStatus({ tone: "success", message: "邮箱配置已保存。" });
    } catch (cause) {
      setStatus({ tone: "error", message: String(cause) });
    } finally {
      setIsSaving(false);
    }
  }

  async function handleTest() {
    if (draft == null) return;
    if (testRecipient.trim() === "") {
      setStatus({ tone: "error", message: "请先填写测试收件邮箱。" });
      return;
    }

    setIsTesting(true);
    setStatus({ tone: "neutral", message: "正在保存配置并发送测试邮件..." });
    try {
      const saved = await persistDraft();
      setDraft(buildDraft(saved));
      const result = await runMailTest({ recipient_email: testRecipient.trim() });
      setStatus({ tone: result.ok ? "success" : "error", message: result.message });
    } catch (cause) {
      setStatus({ tone: "error", message: String(cause) });
    } finally {
      setIsTesting(false);
    }
  }

  if (!isMailSettingsOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/75 p-6 backdrop-blur-sm">
      <div className="h-[min(820px,94vh)] w-[min(980px,96vw)] overflow-hidden rounded-[32px] border border-slate-800 bg-slate-950 shadow-2xl shadow-black/40">
        <section className="h-full overflow-y-auto bg-[radial-gradient(circle_at_top_right,rgba(59,130,246,0.08),transparent_32%),linear-gradient(180deg,rgba(15,23,42,0.98),rgba(2,6,23,1))] px-8 py-7">
          <div className="flex items-start justify-between gap-6">
            <div>
              <div className="text-sm text-slate-400">邮箱设置</div>
              <h3 className="mt-2 text-3xl font-semibold text-slate-50">全局发件邮箱</h3>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-400">
                巡检 Agent 会统一复用这里的 SMTP 配置发通知邮件，发件人固定等于 SMTP 登录邮箱。
              </p>
            </div>
            <button
              className="rounded-2xl border border-slate-700 px-4 py-2 text-sm text-slate-300 transition hover:border-slate-500"
              onClick={closeMailSettings}
            >
              关闭
            </button>
          </div>

          {mailError !== "" && (
            <div className="mt-5 rounded-2xl border border-rose-400/30 bg-rose-400/10 px-4 py-3 text-sm text-rose-300">
              {mailError}
            </div>
          )}
          {isMailSettingsLoading && <div className="mt-5 text-sm text-slate-400">正在加载邮箱配置...</div>}

          {draft != null && (
            <div className="mt-8 space-y-5">
              <section className="rounded-[28px] border border-slate-800 bg-slate-900/80 p-5">
                <div className="grid gap-5 lg:grid-cols-2">
                  <label className="grid gap-2 text-sm">
                    <span className="text-slate-400">邮箱开关</span>
                    <button
                      className={
                        "flex h-12 items-center justify-between rounded-2xl border px-4 text-left transition " +
                        (draft.enabled
                          ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-200"
                          : "border-slate-700 bg-slate-950 text-slate-400")
                      }
                      onClick={() => setDraft({ ...draft, enabled: !draft.enabled })}
                    >
                      <span>{draft.enabled ? "已开启" : "已关闭"}</span>
                      <span className="text-xs">{draft.enabled ? "点击关闭" : "点击开启"}</span>
                    </button>
                  </label>

                  <label className="grid gap-2 text-sm">
                    <span className="text-slate-400">发件邮箱（只读）</span>
                    <input
                      readOnly
                      className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-400 outline-none"
                      value={draft.smtp_username}
                      placeholder="发件邮箱默认等于 SMTP 用户名"
                    />
                  </label>

                  <label className="grid gap-2 text-sm">
                    <span className="text-slate-400">SMTP Host</span>
                    <input
                      className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none placeholder:text-slate-600"
                      value={draft.smtp_host}
                      onChange={(event) => setDraft({ ...draft, smtp_host: event.target.value })}
                      placeholder="例如：smtp.example.com"
                    />
                  </label>

                  <label className="grid gap-2 text-sm">
                    <span className="text-slate-400">SMTP Port</span>
                    <input
                      type="number"
                      min={1}
                      max={65535}
                      className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none"
                      value={draft.smtp_port}
                      onChange={(event) =>
                        setDraft({ ...draft, smtp_port: Number(event.target.value || 587) })
                      }
                    />
                  </label>

                  <label className="grid gap-2 text-sm">
                    <span className="text-slate-400">SMTP 用户名</span>
                    <input
                      className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none placeholder:text-slate-600"
                      value={draft.smtp_username}
                      onChange={(event) => setDraft({ ...draft, smtp_username: event.target.value })}
                      placeholder="例如：bot@example.com"
                    />
                  </label>

                  <label className="grid gap-2 text-sm">
                    <span className="text-slate-400">SMTP 密码</span>
                    <input
                      type="password"
                      className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none placeholder:text-slate-600"
                      value={draft.smtp_password}
                      onChange={(event) => setDraft({ ...draft, smtp_password: event.target.value })}
                      placeholder={mailSettings?.password_masked || "留空表示保留当前密码"}
                    />
                  </label>
                </div>

                <div className="mt-5 grid gap-3 md:grid-cols-2">
                  <label className="flex items-center gap-3 rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm text-slate-300">
                    <input
                      type="checkbox"
                      checked={draft.use_tls}
                      onChange={(event) => setDraft({ ...draft, use_tls: event.target.checked })}
                    />
                    启用 TLS（STARTTLS）
                  </label>
                  <label className="flex items-center gap-3 rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm text-slate-300">
                    <input
                      type="checkbox"
                      checked={draft.use_ssl}
                      onChange={(event) => setDraft({ ...draft, use_ssl: event.target.checked })}
                    />
                    启用 SSL
                  </label>
                </div>
              </section>

              <section className="rounded-[28px] border border-slate-800 bg-slate-900/80 p-5">
                <div className="text-sm font-medium text-slate-100">测试发信</div>
                <p className="mt-2 text-sm leading-6 text-slate-400">
                  填一个测试收件邮箱，系统会先保存当前草稿，再发一封测试邮件。
                </p>
                <div className="mt-5 grid gap-3 md:grid-cols-[minmax(0,1fr)_160px_160px]">
                  <input
                    className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm text-slate-100 outline-none placeholder:text-slate-600"
                    value={testRecipient}
                    onChange={(event) => setTestRecipient(event.target.value)}
                    placeholder="test@example.com"
                  />
                  <button
                    className="rounded-2xl border border-slate-700 px-4 py-3 text-sm text-slate-200 transition hover:border-slate-500 disabled:opacity-60"
                    onClick={() => {
                      void handleTest();
                    }}
                    disabled={isTesting}
                  >
                    {isTesting ? "测试中..." : "测试发信"}
                  </button>
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
              </section>

              <div className={"text-sm " + statusClassName}>{status.message}</div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

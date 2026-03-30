"use client";

import { useEffect, useMemo, useState } from "react";

import { useModelSettings } from "./model-settings-provider";
import type { ProviderConfig, ProviderModel, ProviderProtocol } from "../lib/types";

type ProviderDraft = {
  enabled: boolean;
  protocol: ProviderProtocol;
  api_base_url: string;
  api_key: string;
  models: ProviderModel[];
};

const PROTOCOL_LABELS: Record<ProviderProtocol, { title: string; description: string }> = {
  openai_compatible: {
    title: "OpenAI 兼容",
    description: "适合大多数 OpenAI-compatible 厂商。"
  },
  anthropic_compatible: {
    title: "Anthropic 兼容",
    description: "适合 Anthropic-compatible 协议，如部分 MiniMax 接口。"
  },
  ollama_native: {
    title: "Ollama 原生",
    description: "本地 Ollama 接口，默认会访问 `/api/tags`。"
  },
  mock_local: {
    title: "Learning Mode",
    description: "本地学习模式，不需要真实 API。"
  }
};

function buildDraft(provider: ProviderConfig | undefined): ProviderDraft | null {
  if (provider == null) return null;
  return {
    enabled: provider.enabled,
    protocol: provider.protocol,
    api_base_url: provider.api_base_url,
    api_key: "",
    models: provider.models
  };
}

export function ModelSettingsPanel() {
  const {
    providers,
    error,
    isLoading,
    isModelSettingsOpen,
    selectedProviderId,
    setSelectedProviderId,
    closeModelSettings,
    saveProvider,
    runProviderTest,
    getProvider
  } = useModelSettings();

  const selectedProvider = getProvider(selectedProviderId);
  const [draft, setDraft] = useState<ProviderDraft | null>(null);
  const [newModelLabel, setNewModelLabel] = useState("");
  const [newModelId, setNewModelId] = useState("");
  const [status, setStatus] = useState<{ tone: "neutral" | "success" | "error"; message: string }>({
    tone: "neutral",
    message: ""
  });
  const [isSaving, setIsSaving] = useState(false);
  const [isTesting, setIsTesting] = useState(false);

  useEffect(() => {
    if (!isModelSettingsOpen) return;
    setDraft(buildDraft(selectedProvider));
    setNewModelLabel("");
    setNewModelId("");
    setStatus({ tone: "neutral", message: "" });
  }, [isModelSettingsOpen, selectedProvider]);

  const statusClassName = useMemo(() => {
    if (status.tone === "success") return "text-emerald-300";
    if (status.tone === "error") return "text-rose-300";
    return "text-slate-400";
  }, [status.tone]);

  function addModel() {
    const modelId = newModelId.trim();
    const modelLabel = newModelLabel.trim() || modelId;
    if (draft == null || modelId === "") return;
    if (draft.models.some((model) => model.id === modelId)) {
      setStatus({ tone: "error", message: `模型 \`${modelId}\` 已存在，无需重复添加。` });
      return;
    }
    setDraft({
      ...draft,
      models: draft.models.concat({ id: modelId, label: modelLabel, source: "manual" })
    });
    setNewModelId("");
    setNewModelLabel("");
    setStatus({ tone: "neutral", message: "已加入草稿模型列表，记得点保存配置。" });
  }

  function removeModel(modelId: string) {
    if (draft == null) return;
    setDraft({ ...draft, models: draft.models.filter((model) => model.id !== modelId) });
  }

  async function handleSave() {
    if (selectedProvider == null || draft == null) return;
    setIsSaving(true);
    setStatus({ tone: "neutral", message: "正在保存 provider 配置..." });
    try {
      const payload = {
        enabled: draft.enabled,
        protocol: draft.protocol,
        api_base_url: draft.api_base_url,
        models: draft.models,
        ...(draft.api_key.trim() !== "" ? { api_key: draft.api_key.trim() } : {})
      };
      const saved = await saveProvider(selectedProvider.id, payload);
      setDraft(buildDraft(saved));
      setStatus({ tone: "success", message: `已保存 ${saved.name} 配置。` });
    } catch (cause) {
      setStatus({ tone: "error", message: String(cause) });
    } finally {
      setIsSaving(false);
    }
  }

  async function handleTest() {
    if (selectedProvider == null || draft == null) return;
    setIsTesting(true);
    setStatus({ tone: "neutral", message: "正在测试连接并拉取模型列表..." });
    try {
      const payload = {
        enabled: draft.enabled,
        protocol: draft.protocol,
        api_base_url: draft.api_base_url,
        models: draft.models,
        ...(draft.api_key.trim() !== "" ? { api_key: draft.api_key.trim() } : {})
      };
      const result = await runProviderTest(selectedProvider.id, payload);
      if (!result.ok) {
        setStatus({ tone: "error", message: result.message });
        return;
      }
      const saved = await saveProvider(selectedProvider.id, {
        ...payload,
        models: result.available_models
      });
      setDraft(buildDraft(saved));
      setStatus({ tone: "success", message: "测试成功并已同步模型列表。 " + result.message });
    } catch (cause) {
      setStatus({ tone: "error", message: String(cause) });
    } finally {
      setIsTesting(false);
    }
  }

  if (!isModelSettingsOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/75 p-6 backdrop-blur-sm">
      <div className="grid h-[min(860px,94vh)] w-[min(1240px,96vw)] grid-cols-[320px_minmax(0,1fr)] overflow-hidden rounded-[32px] border border-slate-800 bg-slate-950 shadow-2xl shadow-black/40">
        <aside className="flex min-h-0 flex-col border-r border-slate-800 bg-slate-900/90">
          <div className="border-b border-slate-800 px-5 py-5">
            <div className="text-xs uppercase tracking-[0.35em] text-amber-300">设置</div>
            <h2 className="mt-3 text-2xl font-semibold text-slate-100">模型设置</h2>
            <p className="mt-3 text-sm leading-6 text-slate-400">这里是全局 provider 配置中心。Chat、检索模式和我的 Agent 都会共用这里的设置。</p>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
            {providers.map((provider) => {
              const isActive = provider.id === selectedProviderId;
              return (
                <button
                  key={provider.id}
                  className={
                    "mb-3 w-full rounded-2xl border px-4 py-4 text-left transition " +
                    (isActive
                      ? "border-amber-300/60 bg-amber-300/10"
                      : "border-slate-800 bg-slate-950/60 hover:border-slate-700")
                  }
                  onClick={() => setSelectedProviderId(provider.id)}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="font-medium text-slate-100">{provider.name}</div>
                    <div
                      className={
                        "rounded-full px-2 py-1 text-[11px] " +
                        (provider.enabled ? "bg-emerald-400/15 text-emerald-300" : "bg-slate-800 text-slate-400")
                      }
                    >
                      {provider.enabled ? "已开启" : "已关闭"}
                    </div>
                  </div>
                  <div className="mt-3 flex items-center justify-between text-xs text-slate-400">
                    <span>{provider.protocol}</span>
                    <span>{provider.has_api_key ? "已配置 Key" : "未配置 Key"}</span>
                  </div>
                </button>
              );
            })}
          </div>

          <div className="border-t border-slate-800 px-5 py-4 text-xs leading-6 text-slate-500">
            线程和 Agent 只保存 `provider + model` 引用。真正的 API Key / Base URL 全都来自这里的全局配置。
          </div>
        </aside>

        <section className="min-h-0 overflow-y-auto bg-[radial-gradient(circle_at_top_right,rgba(251,191,36,0.08),transparent_32%),linear-gradient(180deg,rgba(15,23,42,0.98),rgba(2,6,23,1))] px-8 py-7">
          <div className="flex items-start justify-between gap-6">
            <div>
              <div className="text-sm text-slate-400">Provider 详情</div>
              <h3 className="mt-2 text-3xl font-semibold text-slate-50">{selectedProvider?.name ?? "请选择一个 Provider"}</h3>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-400">
                你可以在这里设置协议兼容格式、API Key、Base URL，并通过测试连接把模型列表自动拉回来。
              </p>
            </div>
            <button
              className="rounded-2xl border border-slate-700 px-4 py-2 text-sm text-slate-300 transition hover:border-slate-500"
              onClick={closeModelSettings}
            >
              关闭
            </button>
          </div>

          {error !== "" && <div className="mt-5 rounded-2xl border border-rose-400/30 bg-rose-400/10 px-4 py-3 text-sm text-rose-300">{error}</div>}
          {isLoading && <div className="mt-5 text-sm text-slate-400">正在加载 provider 配置...</div>}

          {selectedProvider != null && draft != null && (
            <>
              {selectedProvider.id === "openai" && (
                <div className="mt-6 rounded-2xl border border-sky-400/25 bg-sky-400/10 px-4 py-3 text-sm leading-6 text-sky-100">
                  这里的 <code className="rounded bg-slate-950/60 px-1 py-0.5">OpenAI</code> 只用于官方 OpenAI。
                  如果你接的是第三方 OpenAI-compatible 网关，请改用 <code className="rounded bg-slate-950/60 px-1 py-0.5">Custom OpenAI Compatible</code>。
                </div>
              )}
              {selectedProvider.id === "custom_openai" && (
                <div className="mt-6 rounded-2xl border border-amber-400/25 bg-amber-400/10 px-4 py-3 text-sm leading-6 text-amber-100">
                  第三方兼容网关（例如自建代理或聚合网关）请配置在这里。模型检查成功后，模型列表会自动同步到 Chat、Agent 和检索模式。
                </div>
              )}
              <div className="mt-8 grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
                <div className="space-y-5">
                  <section className="rounded-[28px] border border-slate-800 bg-slate-900/80 p-5">
                    <div className="grid gap-5 lg:grid-cols-2">
                      <label className="grid gap-2 text-sm">
                        <span className="text-slate-400">Provider 开关</span>
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
                        <span className="text-xs leading-6 text-slate-500">当前版本每次只允许启用一个 provider。开启当前项后，其他 provider 会自动关闭。</span>
                      </label>

                      <label className="grid gap-2 text-sm">
                        <span className="text-slate-400">API Key</span>
                        <input
                          type="password"
                          className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none placeholder:text-slate-600"
                          value={draft.api_key}
                          onChange={(event) => setDraft({ ...draft, api_key: event.target.value })}
                          placeholder={selectedProvider.api_key_masked || "留空表示保留当前 API Key"}
                        />
                      </label>

                      <label className="grid gap-2 text-sm lg:col-span-2">
                        <span className="text-slate-400">API Base URL</span>
                        <input
                          className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none placeholder:text-slate-600"
                          value={draft.api_base_url}
                          onChange={(event) => setDraft({ ...draft, api_base_url: event.target.value })}
                          placeholder="例如：https://api.minimaxi.com/anthropic"
                        />
                      </label>
                    </div>
                  </section>

                  <section className="rounded-[28px] border border-slate-800 bg-slate-900/80 p-5">
                    <div className="text-sm font-medium text-slate-100">API 格式</div>
                    <p className="mt-2 text-sm leading-6 text-slate-400">请选择当前厂商使用的协议兼容格式。MiniMax 这类场景可以在 OpenAI 兼容和 Anthropic 兼容之间切换。</p>
                    <div className="mt-4 grid gap-3 md:grid-cols-2">
                      {selectedProvider.allowed_protocols.map((protocol) => {
                        const meta = PROTOCOL_LABELS[protocol];
                        const isActive = draft.protocol === protocol;
                        return (
                          <button
                            key={protocol}
                            className={
                              "rounded-2xl border px-4 py-4 text-left transition " +
                              (isActive
                                ? "border-sky-400/50 bg-sky-400/10"
                                : "border-slate-700 bg-slate-950 hover:border-slate-600")
                            }
                            onClick={() => setDraft({ ...draft, protocol })}
                          >
                            <div className="font-medium text-slate-100">{meta.title}</div>
                            <div className="mt-2 text-sm leading-6 text-slate-400">{meta.description}</div>
                          </button>
                        );
                      })}
                    </div>
                  </section>

                  <section className="rounded-[28px] border border-slate-800 bg-slate-900/80 p-5">
                    <div className="flex items-center justify-between gap-4">
                      <div>
                        <div className="text-sm font-medium text-slate-100">可用模型列表</div>
                        <p className="mt-2 text-sm leading-6 text-slate-400">你可以手动维护模型，也可以先测试连接，把服务端返回的模型并入草稿列表。</p>
                      </div>
                      <div className="flex gap-3">
                        <button
                          className="rounded-2xl border border-slate-700 px-4 py-2 text-sm text-slate-200 transition hover:border-slate-500 disabled:cursor-not-allowed disabled:opacity-60"
                          onClick={() => {
                            void handleTest();
                          }}
                          disabled={isTesting}
                        >
                          {isTesting ? "测试中..." : "测试连接"}
                        </button>
                        <button
                          className="rounded-2xl bg-amber-300 px-4 py-2 text-sm font-medium text-slate-950 transition hover:bg-amber-200 disabled:cursor-not-allowed disabled:opacity-60"
                          onClick={() => {
                            void handleSave();
                          }}
                          disabled={isSaving}
                        >
                          {isSaving ? "保存中..." : "保存配置"}
                        </button>
                      </div>
                    </div>

                    <div className="mt-5 grid gap-3 lg:grid-cols-[minmax(0,1fr)_220px_120px]">
                      <input
                        className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm text-slate-100 outline-none placeholder:text-slate-600"
                        value={newModelLabel}
                        onChange={(event) => setNewModelLabel(event.target.value)}
                        placeholder="模型显示名，例如 MiniMax M2.5"
                      />
                      <input
                        className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm text-slate-100 outline-none placeholder:text-slate-600"
                        value={newModelId}
                        onChange={(event) => setNewModelId(event.target.value)}
                        placeholder="模型 ID，例如 MiniMax-M2.5"
                      />
                      <button
                        className="rounded-2xl border border-amber-300/40 px-4 py-3 text-sm text-amber-200 transition hover:bg-amber-300/10"
                        onClick={addModel}
                      >
                        添加模型
                      </button>
                    </div>

                    <div className="mt-5 space-y-3">
                      {draft.models.length === 0 && <div className="rounded-2xl border border-dashed border-slate-700 px-4 py-5 text-sm text-slate-500">当前还没有模型。你可以手动添加，或点击“测试连接”尝试自动发现。</div>}
                      {draft.models.map((model) => (
                        <div key={model.id} className="flex items-center justify-between gap-4 rounded-2xl border border-slate-800 bg-slate-950/70 px-4 py-4">
                          <div>
                            <div className="font-medium text-slate-100">{model.label}</div>
                            <div className="mt-1 text-sm text-slate-400">{model.id}</div>
                          </div>
                          <div className="flex items-center gap-3">
                            <span
                              className={
                                "rounded-full px-2 py-1 text-[11px] " +
                                (model.source === "discovered"
                                  ? "bg-sky-400/15 text-sky-300"
                                  : "bg-slate-800 text-slate-400")
                              }
                            >
                              {model.source === "discovered" ? "自动发现" : "手动添加"}
                            </span>
                            <button
                              className="rounded-xl border border-slate-700 px-3 py-2 text-xs text-slate-300 transition hover:border-rose-400 hover:text-rose-300"
                              onClick={() => removeModel(model.id)}
                            >
                              移除
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  </section>
                </div>

                <aside className="space-y-5">
                  <section className="rounded-[28px] border border-slate-800 bg-slate-900/80 p-5">
                    <div className="text-sm font-medium text-slate-100">当前状态</div>
                    <div className="mt-4 space-y-3 text-sm text-slate-400">
                      <div>协议：{draft.protocol}</div>
                      <div>已配置 Key：{selectedProvider.has_api_key || draft.api_key.trim() !== "" ? "是" : "否"}</div>
                      <div>模型数量：{draft.models.length}</div>
                    </div>
                    <div className={"mt-4 text-sm leading-6 " + statusClassName}>
                      {status.message !== "" ? status.message : "保存后，Chat / 检索模式 / 我的 Agent 会立即共用这份 provider 配置。"}
                    </div>
                  </section>

                  <section className="rounded-[28px] border border-slate-800 bg-slate-900/80 p-5">
                    <div className="text-sm font-medium text-slate-100">学习提示</div>
                    <div className="mt-3 space-y-3 text-sm leading-6 text-slate-400">
                      <p>`provider` 是全局配置实体，保存协议、Base URL 和 Key。</p>
                      <p>`model` 只是 provider 下的一个可选项，所以线程和 Agent 只保存引用，不再重复保存密钥。</p>
                      <p>如果切换了 provider 配置，已有线程和 Agent 下次运行就会自动使用新配置。</p>
                    </div>
                  </section>
                </aside>
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  );
}

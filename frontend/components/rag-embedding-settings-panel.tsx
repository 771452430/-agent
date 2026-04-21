"use client";

import { useEffect, useMemo, useState } from "react";

import { useModelSettings } from "./model-settings-provider";

type RagEmbeddingDraft = {
  provider_id: string;
  model: string;
  timeout_seconds: string;
};

function buildDraft() {
  return {
    provider_id: "",
    model: "",
    timeout_seconds: "20"
  };
}

export function RagEmbeddingSettingsPanel() {
  const {
    providers,
    ragEmbeddingSettings,
    ragEmbeddingError,
    isRagEmbeddingSettingsLoading,
    isRagEmbeddingSettingsOpen,
    closeRagEmbeddingSettings,
    saveRagEmbeddingSettings
  } = useModelSettings();
  const [draft, setDraft] = useState<RagEmbeddingDraft>(buildDraft());
  const [status, setStatus] = useState<{ tone: "neutral" | "success" | "error"; message: string }>({
    tone: "neutral",
    message: ""
  });
  const [isSaving, setIsSaving] = useState(false);

  const selectedProvider = useMemo(
    () => providers.find((provider) => provider.id === draft.provider_id),
    [providers, draft.provider_id]
  );

  useEffect(() => {
    if (!isRagEmbeddingSettingsOpen) return;
    setDraft({
      provider_id: ragEmbeddingSettings?.provider_id ?? "",
      model: ragEmbeddingSettings?.model ?? "",
      timeout_seconds: String(ragEmbeddingSettings?.timeout_seconds ?? 20)
    });
    setStatus({ tone: "neutral", message: "" });
  }, [isRagEmbeddingSettingsOpen, ragEmbeddingSettings]);

  const statusClassName = useMemo(() => {
    if (status.tone === "success") return "text-emerald-300";
    if (status.tone === "error") return "text-rose-300";
    return "text-slate-400";
  }, [status.tone]);

  async function handleSave() {
    setIsSaving(true);
    setStatus({ tone: "neutral", message: "正在保存 RAG embedding 设置并重建向量索引..." });
    try {
      const timeoutValue = Number.parseInt(draft.timeout_seconds.trim() || "20", 10);
      await saveRagEmbeddingSettings({
        provider_id: draft.provider_id.trim(),
        model: draft.model.trim(),
        timeout_seconds: Number.isFinite(timeoutValue) ? timeoutValue : 20
      });
      setStatus({ tone: "success", message: "RAG embedding 设置已保存，向量索引已按当前配置重建。" });
    } catch (cause) {
      setStatus({ tone: "error", message: String(cause) });
    } finally {
      setIsSaving(false);
    }
  }

  if (!isRagEmbeddingSettingsOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/75 p-6 backdrop-blur-sm">
      <div className="h-[min(760px,94vh)] w-[min(960px,96vw)] overflow-hidden rounded-[32px] border border-slate-800 bg-slate-950 shadow-2xl shadow-black/40">
        <section className="h-full overflow-y-auto bg-[radial-gradient(circle_at_top_right,rgba(34,197,94,0.12),transparent_34%),linear-gradient(180deg,rgba(15,23,42,0.98),rgba(2,6,23,1))] px-8 py-7">
          <div className="flex items-start justify-between gap-6">
            <div>
              <div className="text-sm text-slate-400">RAG 设置</div>
              <h3 className="mt-2 text-3xl font-semibold text-slate-50">Embedding 运行时</h3>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-400">
                这里决定知识库向量化与查询向量化优先使用哪套 embedding。保存后会自动重建 Chroma
                向量索引，避免“旧索引是 A 模型、新查询却变成 B 模型”的错配。
              </p>
            </div>
            <button
              className="rounded-2xl border border-slate-700 px-4 py-2 text-sm text-slate-300 transition hover:border-slate-500"
              onClick={closeRagEmbeddingSettings}
            >
              关闭
            </button>
          </div>

          {ragEmbeddingError !== "" && (
            <div className="mt-5 rounded-2xl border border-rose-400/30 bg-rose-400/10 px-4 py-3 text-sm text-rose-300">
              {ragEmbeddingError}
            </div>
          )}
          {isRagEmbeddingSettingsLoading && <div className="mt-5 text-sm text-slate-400">正在加载 RAG embedding 设置...</div>}

          <div className="mt-8 grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
            <section className="rounded-[28px] border border-slate-800 bg-slate-900/80 p-5">
              <div className="grid gap-5">
                <label className="grid gap-2 text-sm">
                  <span className="text-slate-400">Embedding Provider</span>
                  <select
                    className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none"
                    value={draft.provider_id}
                    onChange={(event) => setDraft((current) => ({ ...current, provider_id: event.target.value }))}
                  >
                    <option value="">本地 Hashing Fallback</option>
                    {providers.map((provider) => (
                      <option key={provider.id} value={provider.id}>
                        {provider.name}
                        {provider.enabled ? "（当前启用）" : "（未启用）"}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="grid gap-2 text-sm">
                  <span className="text-slate-400">Embedding Model</span>
                  <input
                    type="text"
                    list="rag-embedding-model-options"
                    className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none placeholder:text-slate-600"
                    value={draft.model}
                    onChange={(event) => setDraft((current) => ({ ...current, model: event.target.value }))}
                    placeholder="例如 text-embedding-3-small"
                  />
                  <datalist id="rag-embedding-model-options">
                    {(selectedProvider?.models ?? []).map((model) => (
                      <option key={model.id} value={model.id} />
                    ))}
                  </datalist>
                </label>

                <label className="grid gap-2 text-sm">
                  <span className="text-slate-400">请求超时（秒）</span>
                  <input
                    type="number"
                    min={5}
                    max={120}
                    className="rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-slate-100 outline-none placeholder:text-slate-600"
                    value={draft.timeout_seconds}
                    onChange={(event) => setDraft((current) => ({ ...current, timeout_seconds: event.target.value }))}
                  />
                </label>
              </div>
            </section>

            <section className="rounded-[28px] border border-slate-800 bg-slate-900/80 p-5 text-sm leading-7 text-slate-400">
              <div className="font-medium text-slate-100">当前状态</div>
              <div className="mt-2">配置来源：{ragEmbeddingSettings?.config_source ?? "fallback"}</div>
              <div>运行模式：{ragEmbeddingSettings?.runtime_mode ?? "hashing"}</div>
              <div>期望 backend：{ragEmbeddingSettings?.preferred_backend || "hashing-char-ngram-768-v1"}</div>
              <div>索引 backend：{ragEmbeddingSettings?.indexed_backend || "尚未建索引"}</div>
              <div>是否需要重建：{ragEmbeddingSettings?.reindex_required ? "是" : "否"}</div>

              <div className="mt-5 font-medium text-slate-100">使用说明</div>
              <div className="mt-2">- 若留空 Provider 或 Model，会显式回退到本地 hashing embedding。</div>
              <div>- 若配置来源显示为 `environment`，说明环境变量优先级更高，页面保存不会覆盖运行时生效值。</div>
              <div>- 只有 Provider 已启用、Base URL 可用、协议支持且鉴权完整时，运行模式才会切到 `provider`。</div>
              <div>- 保存后会立刻全量重建知识库向量索引，已有检索结果会按新配置重新生效。</div>
            </section>
          </div>

          <div className="mt-6 flex justify-end">
            <button
              className="rounded-2xl bg-emerald-300 px-4 py-3 text-sm font-medium text-slate-950 transition hover:bg-emerald-200 disabled:opacity-60"
              onClick={() => {
                void handleSave();
              }}
              disabled={isSaving}
            >
              {isSaving ? "保存并重建中..." : "保存并重建索引"}
            </button>
          </div>

          <div className={"mt-4 text-sm " + statusClassName}>{status.message}</div>
        </section>
      </div>
    </div>
  );
}

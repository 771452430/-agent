"use client";

/**
 * 模型选择器。
 *
 * 它把 provider、model、temperature、max tokens 的选择逻辑收口成一个可复用组件。
 */
import { useMemo } from "react";

import { useModelSettings } from "./model-settings-provider";
import type { ModelConfig, ProviderConfig, ProviderModel } from "../lib/types";

type ModelSelectorProps = {
  value: ModelConfig;
  onChange: (next: ModelConfig) => void;
};

function mergeCurrentProvider(enabledProviders: ProviderConfig[], currentProvider?: ProviderConfig) {
  if (currentProvider == null) return enabledProviders;
  if (enabledProviders.some((provider) => provider.id === currentProvider.id)) return enabledProviders;
  return [currentProvider].concat(enabledProviders);
}

export function ModelSelector(props: ModelSelectorProps) {
  const { value, onChange } = props;
  const { isLoading, providers, getProvider, getEnabledProviders, validateModelConfig, openModelSettings } = useModelSettings();

  const enabledProviders = getEnabledProviders();
  const currentProvider = getProvider(value.provider);
  const providerOptions = useMemo(
    () => mergeCurrentProvider(enabledProviders, currentProvider),
    [enabledProviders, currentProvider]
  );
  const provider = currentProvider ?? providerOptions[0];
  const currentModelExists = provider?.models.some((model) => model.id === value.model) ?? false;
  const modelOptions = useMemo(() => {
    if (provider == null) return [];
    if (currentModelExists) return provider.models;
    if (value.model !== "") {
      const invalidModel: ProviderModel = { id: value.model, label: value.model + "（已失效）", source: "manual" };
      return [invalidModel].concat(provider.models);
    }
    return provider.models;
  }, [provider, currentModelExists, value.model]);
  const validation = validateModelConfig(value);

  function pickProviderModeTarget() {
    const enabledRealProvider = enabledProviders.find((item) => item.id !== "mock");
    if (enabledRealProvider != null) {
      return enabledRealProvider;
    }
    const firstRealProvider = providers.find((item) => item.id !== "mock");
    return firstRealProvider;
  }

  function setMode(nextMode: "learning" | "provider") {
    if (nextMode === "learning") {
      onChange({ ...value, mode: "learning" });
      return;
    }

    const targetProvider =
      currentProvider != null && currentProvider.id !== "mock"
        ? currentProvider
        : pickProviderModeTarget();
    onChange({
      ...value,
      mode: "provider",
      provider: targetProvider?.id ?? "",
      model: targetProvider?.models[0]?.id ?? ""
    });
  }

  function handleProviderChange(nextProviderId: string) {
    const nextProvider = getProvider(nextProviderId);
    const nextModelId = nextProvider?.models[0]?.id ?? "";
    onChange({ ...value, provider: nextProviderId, model: nextModelId });
  }

  return (
    <div className="grid gap-3">
      <div className="grid gap-2 text-sm">
        <span className="text-slate-400">运行模式</span>
        <div className="grid grid-cols-2 gap-3">
          <button
            className={
              "apple-segmented rounded-[22px] px-4 py-3 text-left transition " +
              (value.mode === "learning"
                ? "apple-segmented-active text-amber-50"
                : "text-slate-300")
            }
            onClick={() => setMode("learning")}
            type="button"
          >
            <div className="font-medium">Learning Mode</div>
            <div className="mt-1 text-xs text-slate-400">直接走学习模式，不请求真实接口。</div>
          </button>
          <button
            className={
              "apple-segmented rounded-[22px] px-4 py-3 text-left transition " +
              (value.mode === "provider"
                ? "apple-segmented-active text-sky-50"
                : "text-slate-300")
            }
            onClick={() => setMode("provider")}
            type="button"
          >
            <div className="font-medium">真实接口模式</div>
            <div className="mt-1 text-xs text-slate-400">按 provider 配置直接调用真实模型。</div>
          </button>
        </div>
      </div>

      {value.mode === "learning" && (
        <div className="apple-status-warning rounded-[22px] px-3 py-3 text-xs leading-6">
          当前使用 Learning Mode：会展示 LangGraph / Tool / RAG 的完整链路，但不会调用外部模型接口。
        </div>
      )}

      {value.mode === "provider" && (
        <>
          <label className="grid gap-1.5 text-sm">
            <span className="text-slate-400">Provider</span>
            <select
              className="apple-select rounded-[18px] px-3 py-2.5"
              value={value.provider}
              onChange={(event) => handleProviderChange(event.target.value)}
              disabled={isLoading}
            >
              {currentProvider == null && value.provider !== "" && (
                <option value={value.provider}>{value.provider}（已失效）</option>
              )}
              {providerOptions.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                  {!item.enabled ? "（已禁用）" : ""}
                </option>
              ))}
            </select>
          </label>

          <label className="grid gap-1.5 text-sm">
            <span className="text-slate-400">Model</span>
            <select
              className="apple-select rounded-[18px] px-3 py-2.5"
              value={value.model}
              onChange={(event) => onChange({ ...value, model: event.target.value })}
              disabled={isLoading || provider == null}
            >
              {modelOptions.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
        </>
      )}

      {validation.isRunnable ? (
        <div className="apple-status-success rounded-[22px] px-3 py-3 text-xs leading-6">
          {value.mode === "learning"
            ? "当前运行模式可用：Learning Mode"
            : `当前模型配置可用：${validation.provider?.name} / ${validation.model?.label}`}
        </div>
      ) : (
        <div className="apple-status-danger rounded-[22px] px-3 py-3 text-xs leading-6">
          <div>{validation.message}</div>
          <button
            className="mt-2 text-sky-200 underline decoration-dotted underline-offset-4"
            onClick={() => openModelSettings(value.provider)}
          >
            去模型设置修正
          </button>
        </div>
      )}
    </div>
  );
}

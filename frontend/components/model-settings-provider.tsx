"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import {
  getFeishuSettings,
  getMailSettings,
  listProviders,
  testMailSettings,
  testProvider,
  updateFeishuSettings,
  updateMailSettings,
  updateProvider
} from "../lib/api";
import type {
  FeishuSettings,
  MailSettings,
  MailTestRequest,
  MailTestResponse,
  ModelConfig,
  ProviderConfig,
  ProviderModel,
  ProviderTestResponse,
  UpdateMailSettingsRequest,
  UpdateProviderRequest
} from "../lib/types";

type ModelConfigValidation = {
  isRunnable: boolean;
  message: string;
  provider?: ProviderConfig | null;
  model?: ProviderModel | null;
};

type ModelSettingsContextValue = {
  providers: ProviderConfig[];
  isLoading: boolean;
  error: string;
  mailSettings: MailSettings | null;
  isMailSettingsLoading: boolean;
  mailError: string;
  feishuSettings: FeishuSettings | null;
  isFeishuSettingsLoading: boolean;
  feishuError: string;
  isModelSettingsOpen: boolean;
  isMailSettingsOpen: boolean;
  isFeishuSettingsOpen: boolean;
  selectedProviderId: string;
  refreshProviders: () => Promise<void>;
  refreshMailSettings: () => Promise<void>;
  refreshFeishuSettings: () => Promise<void>;
  openModelSettings: (providerId?: string) => void;
  openMailSettings: () => void;
  openFeishuSettings: () => void;
  closeModelSettings: () => void;
  closeMailSettings: () => void;
  closeFeishuSettings: () => void;
  setSelectedProviderId: (providerId: string) => void;
  saveProvider: (providerId: string, input: UpdateProviderRequest) => Promise<ProviderConfig>;
  runProviderTest: (providerId: string, input: UpdateProviderRequest) => Promise<ProviderTestResponse>;
  saveMailSettings: (input: UpdateMailSettingsRequest) => Promise<MailSettings>;
  runMailTest: (input: MailTestRequest) => Promise<MailTestResponse>;
  saveFeishuSettings: (input: { app_id?: string; app_secret?: string }) => Promise<FeishuSettings>;
  validateModelConfig: (config: ModelConfig) => ModelConfigValidation;
  getProvider: (providerId: string) => ProviderConfig | undefined;
  getEnabledProviders: () => ProviderConfig[];
};

const ModelSettingsContext = createContext<ModelSettingsContextValue | null>(null);

function sortProviders(providers: ProviderConfig[]) {
  return providers.slice().sort((left, right) => left.name.localeCompare(right.name, "zh-CN"));
}

export function ModelSettingsProvider(props: { children: ReactNode }) {
  const [providers, setProviders] = useState<ProviderConfig[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const [mailSettings, setMailSettings] = useState<MailSettings | null>(null);
  const [isMailSettingsLoading, setIsMailSettingsLoading] = useState(true);
  const [mailError, setMailError] = useState("");
  const [feishuSettings, setFeishuSettings] = useState<FeishuSettings | null>(null);
  const [isFeishuSettingsLoading, setIsFeishuSettingsLoading] = useState(true);
  const [feishuError, setFeishuError] = useState("");
  const [isModelSettingsOpen, setIsModelSettingsOpen] = useState(false);
  const [isMailSettingsOpen, setIsMailSettingsOpen] = useState(false);
  const [isFeishuSettingsOpen, setIsFeishuSettingsOpen] = useState(false);
  const [selectedProviderId, setSelectedProviderId] = useState("");

  async function refreshProviders() {
    setError("");
    const nextProviders = sortProviders(await listProviders());
    setProviders(nextProviders);
    setSelectedProviderId((current) => {
      if (current !== "" && nextProviders.some((provider) => provider.id === current)) {
        return current;
      }
      return nextProviders[0]?.id ?? "";
    });
  }

  async function refreshMailSettings() {
    setMailError("");
    setMailSettings(await getMailSettings());
  }

  async function refreshFeishuSettings() {
    setFeishuError("");
    setFeishuSettings(await getFeishuSettings());
  }

  useEffect(() => {
    refreshProviders()
      .catch((cause) => setError(String(cause)))
      .finally(() => setIsLoading(false));
    refreshMailSettings()
      .catch((cause) => setMailError(String(cause)))
      .finally(() => setIsMailSettingsLoading(false));
    refreshFeishuSettings()
      .catch((cause) => setFeishuError(String(cause)))
      .finally(() => setIsFeishuSettingsLoading(false));
  }, []);

  const providersById = useMemo(() => {
    return new Map(providers.map((provider) => [provider.id, provider]));
  }, [providers]);

  function replaceProvider(nextProvider: ProviderConfig) {
    setProviders((current) => sortProviders(current.filter((provider) => provider.id !== nextProvider.id).concat(nextProvider)));
  }

  function openModelSettings(providerId?: string) {
    setIsMailSettingsOpen(false);
    setIsFeishuSettingsOpen(false);
    if (providerId != null && providerId !== "") {
      setSelectedProviderId(providerId);
    } else if (selectedProviderId === "" && providers.length > 0) {
      setSelectedProviderId(providers[0].id);
    }
    setIsModelSettingsOpen(true);
  }

  function openMailSettings() {
    setIsModelSettingsOpen(false);
    setIsFeishuSettingsOpen(false);
    setIsMailSettingsOpen(true);
  }

  function openFeishuSettings() {
    setIsModelSettingsOpen(false);
    setIsMailSettingsOpen(false);
    setIsFeishuSettingsOpen(true);
  }

  function closeModelSettings() {
    setIsModelSettingsOpen(false);
  }

  function closeMailSettings() {
    setIsMailSettingsOpen(false);
  }

  function closeFeishuSettings() {
    setIsFeishuSettingsOpen(false);
  }

  async function saveProvider(providerId: string, input: UpdateProviderRequest) {
    const saved = await updateProvider(providerId, input);
    await refreshProviders();
    replaceProvider(saved);
    return saved;
  }

  async function runProviderTest(providerId: string, input: UpdateProviderRequest) {
    return testProvider(providerId, input);
  }

  async function saveMailSettings(input: UpdateMailSettingsRequest) {
    const saved = await updateMailSettings(input);
    setMailSettings(saved);
    return saved;
  }

  async function runMailTest(input: MailTestRequest) {
    return testMailSettings(input);
  }

  async function saveFeishuSettings(input: { app_id?: string; app_secret?: string }) {
    const saved = await updateFeishuSettings(input);
    setFeishuSettings(saved);
    return saved;
  }

  function getProvider(providerId: string) {
    return providersById.get(providerId);
  }

  function getEnabledProviders() {
    return providers.filter((provider) => provider.enabled);
  }

  function validateModelConfig(config: ModelConfig): ModelConfigValidation {
    if (isLoading) {
      return { isRunnable: false, message: "模型配置加载中，请稍候。" };
    }

    if (config.mode === "learning") {
      const learningProvider = providersById.get("mock") ?? null;
      const learningModel = learningProvider?.models.find((item) => item.id === "learning-mode") ?? null;
      return { isRunnable: true, message: "", provider: learningProvider, model: learningModel };
    }

    const provider = providersById.get(config.provider);
    if (provider == null) {
      return { isRunnable: false, message: `当前 provider \`${config.provider}\` 不存在，请到模型设置修正。`, provider: null };
    }
    if (!provider.enabled) {
      return { isRunnable: false, message: `Provider \`${provider.name}\` 当前已禁用，请到模型设置重新开启。`, provider };
    }
    if (provider.models.length === 0) {
      return { isRunnable: false, message: `Provider \`${provider.name}\` 还没有模型，请先在模型设置中添加。`, provider };
    }

    const model = provider.models.find((item) => item.id === config.model);
    if (model == null) {
      const customOpenAI = providersById.get("custom_openai");
      const migratedToCustom =
        provider.id === "openai" &&
        (customOpenAI?.models.some((item) => item.id === config.model) ?? false);
      return {
        isRunnable: false,
        message: migratedToCustom
          ? `模型 \`${config.model}\` 已从官方 OpenAI 配置迁移到 Custom OpenAI Compatible，请切换 provider 后再运行。`
          : `模型 \`${config.model}\` 已失效或不存在，请到模型设置修正。`,
        provider,
        model: null
      };
    }

    return { isRunnable: true, message: "", provider, model };
  }

  return (
    <ModelSettingsContext.Provider
      value={{
        providers,
        isLoading,
        error,
        mailSettings,
        isMailSettingsLoading,
        mailError,
        feishuSettings,
        isFeishuSettingsLoading,
        feishuError,
        isModelSettingsOpen,
        isMailSettingsOpen,
        isFeishuSettingsOpen,
        selectedProviderId,
        refreshProviders,
        refreshMailSettings,
        refreshFeishuSettings,
        openModelSettings,
        openMailSettings,
        openFeishuSettings,
        closeModelSettings,
        closeMailSettings,
        closeFeishuSettings,
        setSelectedProviderId,
        saveProvider,
        runProviderTest,
        saveMailSettings,
        runMailTest,
        saveFeishuSettings,
        validateModelConfig,
        getProvider,
        getEnabledProviders
      }}
    >
      {props.children}
    </ModelSettingsContext.Provider>
  );
}

export function useModelSettings() {
  const context = useContext(ModelSettingsContext);
  if (context == null) {
    throw new Error("useModelSettings must be used inside ModelSettingsProvider");
  }
  return context;
}

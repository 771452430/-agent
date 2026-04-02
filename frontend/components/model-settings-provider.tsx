"use client";

/**
 * 模型设置上下文提供者。
 *
 * 它把 provider 列表、模型可用性校验、邮箱设置、飞书设置和设置弹窗状态
 * 统一放进一个 React Context 中，方便全站任何页面直接复用。
 */
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import {
  getFeishuSettings,
  getGitLabImportSettings,
  getMailSettings,
  getWorkNotifySettings,
  listProviders,
  testMailSettings,
  testProvider,
  updateFeishuSettings,
  updateGitLabImportSettings,
  updateMailSettings,
  updateWorkNotifySettings,
  updateProvider
} from "../lib/api";
import type {
  FeishuSettings,
  GitLabImportSettings,
  MailSettings,
  MailTestRequest,
  MailTestResponse,
  ModelConfig,
  ProviderConfig,
  ProviderModel,
  ProviderTestResponse,
  UpdateMailSettingsRequest,
  UpdateProviderRequest,
  UpdateWorkNotifySettingsRequest,
  WorkNotifySettings
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
  workNotifySettings: WorkNotifySettings | null;
  isWorkNotifySettingsLoading: boolean;
  workNotifyError: string;
  gitlabImportSettings: GitLabImportSettings | null;
  isGitLabImportSettingsLoading: boolean;
  gitlabImportError: string;
  isModelSettingsOpen: boolean;
  isMailSettingsOpen: boolean;
  isFeishuSettingsOpen: boolean;
  isWorkNotifySettingsOpen: boolean;
  isGitLabImportSettingsOpen: boolean;
  selectedProviderId: string;
  refreshProviders: () => Promise<void>;
  refreshMailSettings: () => Promise<void>;
  refreshFeishuSettings: () => Promise<void>;
  refreshWorkNotifySettings: () => Promise<void>;
  refreshGitLabImportSettings: () => Promise<void>;
  openModelSettings: (providerId?: string) => void;
  openMailSettings: () => void;
  openFeishuSettings: () => void;
  openWorkNotifySettings: () => void;
  openGitLabImportSettings: () => void;
  closeModelSettings: () => void;
  closeMailSettings: () => void;
  closeFeishuSettings: () => void;
  closeWorkNotifySettings: () => void;
  closeGitLabImportSettings: () => void;
  setSelectedProviderId: (providerId: string) => void;
  saveProvider: (providerId: string, input: UpdateProviderRequest) => Promise<ProviderConfig>;
  runProviderTest: (providerId: string, input: UpdateProviderRequest) => Promise<ProviderTestResponse>;
  saveMailSettings: (input: UpdateMailSettingsRequest) => Promise<MailSettings>;
  runMailTest: (input: MailTestRequest) => Promise<MailTestResponse>;
  saveFeishuSettings: (input: { app_id?: string; app_secret?: string }) => Promise<FeishuSettings>;
  saveWorkNotifySettings: (input: UpdateWorkNotifySettingsRequest) => Promise<WorkNotifySettings>;
  saveGitLabImportSettings: (input: {
    token?: string;
    clear_token?: boolean;
    allowed_hosts?: string[];
  }) => Promise<GitLabImportSettings>;
  validateModelConfig: (config: ModelConfig) => ModelConfigValidation;
  getProvider: (providerId: string) => ProviderConfig | undefined;
  getEnabledProviders: () => ProviderConfig[];
};

/** Context 负责把模型设置相关状态分发到整棵组件树。 */
const ModelSettingsContext = createContext<ModelSettingsContextValue | null>(null);

/** 让 provider 在界面上的排列顺序稳定，避免每次刷新顺序乱跳。 */
function sortProviders(providers: ProviderConfig[]) {
  return providers.slice().sort((left, right) => left.name.localeCompare(right.name, "zh-CN"));
}

export function ModelSettingsProvider(props: { children: ReactNode }) {
  // 这一组 state 描述“设置中心自身”的运行状态：
  // 当前有哪些 provider、是否还在加载、最近的错误是什么、哪些弹窗已打开。
  const [providers, setProviders] = useState<ProviderConfig[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const [mailSettings, setMailSettings] = useState<MailSettings | null>(null);
  const [isMailSettingsLoading, setIsMailSettingsLoading] = useState(true);
  const [mailError, setMailError] = useState("");
  const [feishuSettings, setFeishuSettings] = useState<FeishuSettings | null>(null);
  const [isFeishuSettingsLoading, setIsFeishuSettingsLoading] = useState(true);
  const [feishuError, setFeishuError] = useState("");
  const [workNotifySettings, setWorkNotifySettings] = useState<WorkNotifySettings | null>(null);
  const [isWorkNotifySettingsLoading, setIsWorkNotifySettingsLoading] = useState(true);
  const [workNotifyError, setWorkNotifyError] = useState("");
  const [gitlabImportSettings, setGitlabImportSettings] = useState<GitLabImportSettings | null>(null);
  const [isGitLabImportSettingsLoading, setIsGitLabImportSettingsLoading] = useState(true);
  const [gitlabImportError, setGitlabImportError] = useState("");
  const [isModelSettingsOpen, setIsModelSettingsOpen] = useState(false);
  const [isMailSettingsOpen, setIsMailSettingsOpen] = useState(false);
  const [isFeishuSettingsOpen, setIsFeishuSettingsOpen] = useState(false);
  const [isWorkNotifySettingsOpen, setIsWorkNotifySettingsOpen] = useState(false);
  const [isGitLabImportSettingsOpen, setIsGitLabImportSettingsOpen] = useState(false);
  const [selectedProviderId, setSelectedProviderId] = useState("");

  async function refreshProviders() {
    // 刷新 provider 后需要顺便校正当前选中的 providerId，
    // 否则如果旧 provider 被删除或禁用，右侧面板就会失去目标。
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

  async function refreshWorkNotifySettings() {
    setWorkNotifyError("");
    setWorkNotifySettings(await getWorkNotifySettings());
  }

  async function refreshGitLabImportSettings() {
    setGitlabImportError("");
    setGitlabImportSettings(await getGitLabImportSettings());
  }

  useEffect(() => {
    // 首次挂载时并行拉取三类全局配置：
    // provider、邮箱、飞书。这样设置面板一打开就有完整上下文。
    refreshProviders()
      .catch((cause) => setError(String(cause)))
      .finally(() => setIsLoading(false));
    refreshMailSettings()
      .catch((cause) => setMailError(String(cause)))
      .finally(() => setIsMailSettingsLoading(false));
    refreshFeishuSettings()
      .catch((cause) => setFeishuError(String(cause)))
      .finally(() => setIsFeishuSettingsLoading(false));
    refreshWorkNotifySettings()
      .catch((cause) => setWorkNotifyError(String(cause)))
      .finally(() => setIsWorkNotifySettingsLoading(false));
    refreshGitLabImportSettings()
      .catch((cause) => setGitlabImportError(String(cause)))
      .finally(() => setIsGitLabImportSettingsLoading(false));
  }, []);

  const providersById = useMemo(() => {
    // 后面会频繁按 id 查询 provider，因此先转成 Map，
    // 可以让读取逻辑更直接，也避免反复遍历数组。
    return new Map(providers.map((provider) => [provider.id, provider]));
  }, [providers]);

  function replaceProvider(nextProvider: ProviderConfig) {
    setProviders((current) => sortProviders(current.filter((provider) => provider.id !== nextProvider.id).concat(nextProvider)));
  }

  function openModelSettings(providerId?: string) {
    setIsMailSettingsOpen(false);
    setIsFeishuSettingsOpen(false);
    setIsWorkNotifySettingsOpen(false);
    setIsGitLabImportSettingsOpen(false);
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
    setIsWorkNotifySettingsOpen(false);
    setIsGitLabImportSettingsOpen(false);
    setIsMailSettingsOpen(true);
  }

  function openFeishuSettings() {
    setIsModelSettingsOpen(false);
    setIsMailSettingsOpen(false);
    setIsWorkNotifySettingsOpen(false);
    setIsGitLabImportSettingsOpen(false);
    setIsFeishuSettingsOpen(true);
  }

  function openWorkNotifySettings() {
    setIsModelSettingsOpen(false);
    setIsMailSettingsOpen(false);
    setIsFeishuSettingsOpen(false);
    setIsGitLabImportSettingsOpen(false);
    setIsWorkNotifySettingsOpen(true);
  }

  function openGitLabImportSettings() {
    setIsModelSettingsOpen(false);
    setIsMailSettingsOpen(false);
    setIsFeishuSettingsOpen(false);
    setIsWorkNotifySettingsOpen(false);
    setIsGitLabImportSettingsOpen(true);
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

  function closeWorkNotifySettings() {
    setIsWorkNotifySettingsOpen(false);
  }

  function closeGitLabImportSettings() {
    setIsGitLabImportSettingsOpen(false);
  }

  async function saveProvider(providerId: string, input: UpdateProviderRequest) {
    // 保存后既刷新远端真值，也即时替换本地缓存，确保界面马上反映结果。
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

  async function saveWorkNotifySettings(input: UpdateWorkNotifySettingsRequest) {
    const saved = await updateWorkNotifySettings(input);
    setWorkNotifySettings(saved);
    return saved;
  }

  async function saveGitLabImportSettings(input: {
    token?: string;
    clear_token?: boolean;
    allowed_hosts?: string[];
  }) {
    const saved = await updateGitLabImportSettings(input);
    setGitlabImportSettings(saved);
    return saved;
  }

  function getProvider(providerId: string) {
    return providersById.get(providerId);
  }

  function getEnabledProviders() {
    return providers.filter((provider) => provider.enabled);
  }

  function validateModelConfig(config: ModelConfig): ModelConfigValidation {
    // 这是页面真正发请求前的最后一道前端校验：
    // provider 是否存在、是否启用、模型名是否还在当前 provider 下可选。
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
        workNotifySettings,
        isWorkNotifySettingsLoading,
        workNotifyError,
        gitlabImportSettings,
        isGitLabImportSettingsLoading,
        gitlabImportError,
        isModelSettingsOpen,
        isMailSettingsOpen,
        isFeishuSettingsOpen,
        isWorkNotifySettingsOpen,
        isGitLabImportSettingsOpen,
        selectedProviderId,
        refreshProviders,
        refreshMailSettings,
        refreshFeishuSettings,
        refreshWorkNotifySettings,
        refreshGitLabImportSettings,
        openModelSettings,
        openMailSettings,
        openFeishuSettings,
        openWorkNotifySettings,
        openGitLabImportSettings,
        closeModelSettings,
        closeMailSettings,
        closeFeishuSettings,
        closeWorkNotifySettings,
        closeGitLabImportSettings,
        setSelectedProviderId,
        saveProvider,
        runProviderTest,
        saveMailSettings,
        runMailTest,
        saveFeishuSettings,
        saveWorkNotifySettings,
        saveGitLabImportSettings,
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

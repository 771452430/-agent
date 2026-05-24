"use client";

import { useEffect, useMemo, useState } from "react";

import { draftJiraSolutionReply, searchJiraSolution } from "../lib/api";
import type {
  JiraDuplicateCandidate,
  JiraDuplicateIssueResult,
  JiraDuplicateMatchLevel,
  JiraSolutionDraftReplyResponse,
  ModelConfig
} from "../lib/types";
import { ModelSelector } from "./model-selector";

const DEFAULT_SOURCE_DB_PATH = "backend/data/jira/jira_support.db";
const CONFIG_STORAGE_KEY = "jira-solution-search-config-v1";

const DEFAULT_MODEL: ModelConfig = {
  mode: "learning",
  provider: "mock",
  model: "learning-mode",
  temperature: 0.2,
  max_tokens: 1024
};

function matchLabel(level: JiraDuplicateMatchLevel) {
  if (level === "high") return "建议复用";
  if (level === "medium") return "人工判断";
  if (level === "low") return "弱相似";
  return "未命中";
}

function matchBadge(level: JiraDuplicateMatchLevel) {
  if (level === "high") return "border-emerald-300/30 bg-emerald-400/10 text-emerald-100";
  if (level === "medium") return "border-amber-300/30 bg-amber-400/10 text-amber-100";
  if (level === "low") return "border-slate-300/20 bg-slate-400/10 text-slate-200";
  return "border-rose-300/25 bg-rose-400/10 text-rose-100";
}

function highConfidenceCandidates(result: JiraDuplicateIssueResult | null) {
  return (result?.candidates ?? []).filter((candidate) => candidate.score >= 0.78).sort((left, right) => right.score - left.score);
}

function readStoredConfig() {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(CONFIG_STORAGE_KEY);
  if (raw == null || raw.trim() === "") return null;
  try {
    return JSON.parse(raw) as {
      sourceDbPath?: string;
      highThreshold?: number;
      mediumThreshold?: number;
      modelReviewEnabled?: boolean;
      modelConfig?: ModelConfig;
    };
  } catch {
    return null;
  }
}

function CandidateCard(props: { candidate: JiraDuplicateCandidate }) {
  const { candidate } = props;
  return (
    <article className="apple-panel-subtle rounded-[20px] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-sky-100">{candidate.issue_key}</div>
          <h3 className="mt-1 text-base font-semibold leading-6 text-white">{candidate.summary || "历史已完成工单"}</h3>
        </div>
        <span className="apple-pill rounded-full px-3 py-1 text-xs text-slate-200">
          {(candidate.score * 100).toFixed(1)}%
        </span>
      </div>
      <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-300">
        {candidate.domain && <span className="apple-pill rounded-full px-2.5 py-1">{candidate.domain}</span>}
        {candidate.module && <span className="apple-pill rounded-full px-2.5 py-1">{candidate.module}</span>}
        {candidate.status && <span className="apple-pill rounded-full px-2.5 py-1">{candidate.status}</span>}
      </div>
      {candidate.reason && <p className="mt-3 text-sm leading-6 text-slate-300">{candidate.reason}</p>}
      <div className="mt-4 rounded-[18px] border border-emerald-200/10 bg-emerald-300/5 p-4">
        <div className="text-xs font-medium uppercase text-emerald-100">解决方案</div>
        <p className="mt-2 whitespace-pre-wrap text-sm leading-7 text-slate-100">{candidate.solution || "历史工单未记录解决方案。"}</p>
      </div>
    </article>
  );
}

function DraftReplyModal(props: {
  isOpen: boolean;
  isGenerating: boolean;
  draftReplyText: string;
  onDraftReplyTextChange: (value: string) => void;
  onClose: () => void;
  onCopy: () => void;
  copyFeedback: string;
  draftReplyMeta: JiraSolutionDraftReplyResponse | null;
  errorMessage: string;
}) {
  const { isOpen, isGenerating, draftReplyText, onDraftReplyTextChange, onClose, onCopy, copyFeedback, draftReplyMeta, errorMessage } = props;

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/75 p-6 backdrop-blur-sm">
      <div className="apple-window w-[min(920px,96vw)] overflow-hidden rounded-[34px]">
        <div className="border-b border-white/10 px-6 py-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="apple-kicker">Reply Draft</div>
              <h2 className="mt-2 text-2xl font-semibold text-white">客户回复草稿</h2>
              {draftReplyMeta && (
                <p className="mt-2 text-sm leading-6 text-slate-300">
                  {draftReplyMeta.message} 当前来源：{draftReplyMeta.model_label}
                </p>
              )}
            </div>
            <button className="apple-button-secondary rounded-full px-4 py-2 text-sm" type="button" onClick={onClose}>
              关闭
            </button>
          </div>
        </div>
        <div className="space-y-4 px-6 py-5">
          {errorMessage && <div className="apple-status-danger rounded-[18px] px-4 py-3 text-sm">{errorMessage}</div>}
          <textarea
            className="apple-textarea min-h-[360px] w-full resize-y rounded-[22px] px-4 py-4 leading-7"
            value={draftReplyText}
            onChange={(event) => onDraftReplyTextChange(event.target.value)}
            placeholder={isGenerating ? "正在生成回复草稿..." : "这里会显示可编辑的回复草稿。"}
            disabled={isGenerating}
          />
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="text-sm text-slate-400">{copyFeedback || "你可以先润色语气，再直接复制给客户使用。"}</div>
            <div className="flex flex-wrap gap-3">
              <button
                className="apple-button-secondary rounded-full px-4 py-2.5 text-sm"
                type="button"
                onClick={onClose}
              >
                关闭
              </button>
              <button
                className="apple-button-primary rounded-full px-5 py-2.5 text-sm font-medium disabled:opacity-50"
                type="button"
                disabled={isGenerating || draftReplyText.trim() === ""}
                onClick={onCopy}
              >
                复制
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ResultPanel(props: {
  result: JiraDuplicateIssueResult | null;
  isDraftingReply: boolean;
  canDraftReply: boolean;
  onDraftReply: () => void;
}) {
  const { result, isDraftingReply, canDraftReply, onDraftReply } = props;
  const sortedCandidates = useMemo(
    () => (result?.candidates ?? []).slice().sort((left, right) => right.score - left.score),
    [result]
  );

  if (result == null) {
    return (
      <section className="apple-panel rounded-[24px] p-6">
        <div className="apple-kicker">Search Result</div>
        <h2 className="mt-3 text-xl font-semibold text-white">等待检索</h2>
        <p className="mt-3 text-sm leading-6 text-slate-300">粘贴问题描述后，这里会展示历史已完成工单和解决方案候选。</p>
      </section>
    );
  }

  return (
      <section className="apple-panel rounded-[24px] p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="apple-kicker">Search Result</div>
            <h2 className="mt-3 text-xl font-semibold text-white">{result.title || result.issue_key}</h2>
            <p className="mt-2 text-sm leading-6 text-slate-300">{result.match_reason}</p>
          </div>
          <div className="flex flex-col items-end gap-3">
            <span className={"rounded-full border px-3 py-1.5 text-sm " + matchBadge(result.match_level)}>
              {matchLabel(result.match_level)} · {(result.match_score * 100).toFixed(1)}%
            </span>
            <button
              className="apple-button-secondary rounded-full px-4 py-2 text-sm disabled:opacity-50"
              type="button"
              disabled={!canDraftReply || isDraftingReply}
              onClick={onDraftReply}
            >
              {isDraftingReply ? "生成中..." : "生成回复草稿"}
            </button>
          </div>
        </div>

      {sortedCandidates.length > 0 ? (
        <div className="mt-5 grid gap-4">
          {sortedCandidates.map((candidate) => (
            <CandidateCard key={candidate.issue_key} candidate={candidate} />
          ))}
        </div>
      ) : (
        <div className="mt-5 apple-panel-subtle rounded-[20px] p-5 text-sm leading-6 text-slate-300">
          没有达到展示阈值的历史解决方案候选。
        </div>
      )}
    </section>
  );
}

export function JiraSolutionSearchWorkspace() {
  const [description, setDescription] = useState("");
  const [issueKey, setIssueKey] = useState("MANUAL-QUERY");
  const [domain, setDomain] = useState("");
  const [module, setModule] = useState("");
  const [sourceDbPath, setSourceDbPath] = useState(DEFAULT_SOURCE_DB_PATH);
  const [highThreshold, setHighThreshold] = useState(0.78);
  const [mediumThreshold, setMediumThreshold] = useState(0.55);
  const [modelReviewEnabled, setModelReviewEnabled] = useState(false);
  const [modelConfig, setModelConfig] = useState<ModelConfig>(DEFAULT_MODEL);
  const [isConfigOpen, setIsConfigOpen] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const [isSavingConfig, setIsSavingConfig] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [configNotice, setConfigNotice] = useState("");
  const [result, setResult] = useState<JiraDuplicateIssueResult | null>(null);
  const [indexInfo, setIndexInfo] = useState<{ indexed_count: number; embedding_backend: string } | null>(null);
  const [isDraftModalOpen, setIsDraftModalOpen] = useState(false);
  const [isDraftingReply, setIsDraftingReply] = useState(false);
  const [draftReplyText, setDraftReplyText] = useState("");
  const [draftReplyError, setDraftReplyError] = useState("");
  const [copyFeedback, setCopyFeedback] = useState("");
  const [draftReplyMeta, setDraftReplyMeta] = useState<JiraSolutionDraftReplyResponse | null>(null);
  const draftCandidates = useMemo(() => highConfidenceCandidates(result), [result]);
  const canDraftReply = result?.match_level === "high" && draftCandidates.length > 0;

  useEffect(() => {
    const stored = readStoredConfig();
    if (stored == null) return;
    if (typeof stored.sourceDbPath === "string" && stored.sourceDbPath.trim() !== "") {
      setSourceDbPath(stored.sourceDbPath);
    }
    if (typeof stored.highThreshold === "number") setHighThreshold(stored.highThreshold);
    if (typeof stored.mediumThreshold === "number") setMediumThreshold(stored.mediumThreshold);
    if (typeof stored.modelReviewEnabled === "boolean") setModelReviewEnabled(stored.modelReviewEnabled);
    if (stored.modelConfig != null) setModelConfig(stored.modelConfig);
    setConfigNotice("已加载本地保存的检索配置。");
  }, []);

  async function handleSearch() {
    const normalized = description.trim();
    if (normalized === "") {
      setErrorMessage("请先粘贴问题描述。");
      return;
    }
    setIsSearching(true);
    setErrorMessage("");
    setDraftReplyError("");
    setCopyFeedback("");
    try {
      const response = await searchJiraSolution({
        description: normalized,
        issue_key: issueKey.trim() || "MANUAL-QUERY",
        source_db_path: sourceDbPath.trim() || DEFAULT_SOURCE_DB_PATH,
        domain: domain.trim(),
        module: module.trim(),
        status: "待分析",
        high_similarity_threshold: highThreshold,
        medium_similarity_threshold: mediumThreshold,
        model_review_enabled: modelReviewEnabled,
        model_config: modelConfig
      });
      setResult(response.result);
      setIndexInfo({
        indexed_count: response.indexed_count,
        embedding_backend: response.embedding_backend
      });
      setIsDraftModalOpen(false);
      setDraftReplyText("");
      setDraftReplyMeta(null);
    } catch (cause) {
      setErrorMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setIsSearching(false);
    }
  }

  async function handleDraftReply() {
    if (result == null || canDraftReply === false) return;
    setIsDraftingReply(true);
    setDraftReplyError("");
    setCopyFeedback("");
    setIsDraftModalOpen(true);
    try {
      const response = await draftJiraSolutionReply({
        description: description.trim(),
        result,
        candidates: draftCandidates,
        model_config: modelConfig
      });
      setDraftReplyText(response.draft_text);
      setDraftReplyMeta(response);
    } catch (cause) {
      setDraftReplyText("");
      setDraftReplyMeta(null);
      setDraftReplyError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setIsDraftingReply(false);
    }
  }

  async function handleCopyDraft() {
    try {
      await navigator.clipboard.writeText(draftReplyText);
      setCopyFeedback("已复制当前草稿内容。");
    } catch {
      setCopyFeedback("复制失败，请手动选中文本复制。");
    }
  }

  async function handleSaveConfig() {
    setIsSavingConfig(true);
    try {
      window.localStorage.setItem(
        CONFIG_STORAGE_KEY,
        JSON.stringify({
          sourceDbPath,
          highThreshold,
          mediumThreshold,
          modelReviewEnabled,
          modelConfig
        })
      );
      setConfigNotice("检索配置已保存到当前浏览器。");
    } catch (cause) {
      setConfigNotice(cause instanceof Error ? `保存失败：${cause.message}` : `保存失败：${String(cause)}`);
    } finally {
      setIsSavingConfig(false);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <header className="border-b border-white/10 px-5 py-5 md:px-7">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="apple-kicker">Jira Solution Agent</div>
            <h1 className="mt-2 text-2xl font-semibold text-white">Jira 方案检索 Agent</h1>
          </div>
          <button
            className="apple-button-secondary rounded-full px-4 py-2 text-sm"
            type="button"
            onClick={() => setIsConfigOpen((value) => !value)}
          >
            {isConfigOpen ? "收起配置" : "配置"}
          </button>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto px-5 py-5 md:px-7">
        <div className="grid gap-5 xl:grid-cols-[minmax(0,0.92fr)_minmax(0,1.08fr)]">
          <section className="grid gap-5">
            <div className="apple-panel rounded-[24px] p-6">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="apple-kicker">Manual Query</div>
                  <h2 className="mt-3 text-xl font-semibold text-white">问题描述</h2>
                </div>
                {indexInfo && (
                  <span className="apple-pill rounded-full px-3 py-1 text-xs text-slate-300">
                    索引 {indexInfo.indexed_count} 条 · {indexInfo.embedding_backend || "embedding"}
                  </span>
                )}
              </div>

              <label className="mt-5 grid gap-2 text-sm">
                <span className="text-slate-400">问题文案</span>
                <textarea
                  className="apple-textarea min-h-[220px] resize-y rounded-[20px] px-4 py-3 leading-7"
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  placeholder="【DSP支持问题】打了330补丁合集之后，ca用户登不上了，驱动加载正常，一点登陆页面就会刷新。"
                />
              </label>

              <div className="mt-4 grid gap-3 sm:grid-cols-3">
                <label className="grid gap-2 text-sm">
                  <span className="text-slate-400">Jira 号</span>
                  <input
                    className="apple-input rounded-[18px] px-3 py-2.5"
                    value={issueKey}
                    onChange={(event) => setIssueKey(event.target.value)}
                  />
                </label>
                <label className="grid gap-2 text-sm">
                  <span className="text-slate-400">领域</span>
                  <input
                    className="apple-input rounded-[18px] px-3 py-2.5"
                    value={domain}
                    onChange={(event) => setDomain(event.target.value)}
                    placeholder="工作台"
                  />
                </label>
                <label className="grid gap-2 text-sm">
                  <span className="text-slate-400">模块</span>
                  <input
                    className="apple-input rounded-[18px] px-3 py-2.5"
                    value={module}
                    onChange={(event) => setModule(event.target.value)}
                    placeholder="登录入口与配置"
                  />
                </label>
              </div>

              {errorMessage && <div className="mt-4 apple-status-danger rounded-[18px] px-4 py-3 text-sm">{errorMessage}</div>}

              <div className="mt-5 flex flex-wrap gap-3">
                <button
                  className="apple-button-primary rounded-full px-5 py-2.5 text-sm font-medium"
                  type="button"
                  disabled={isSearching}
                  onClick={() => void handleSearch()}
                >
                  {isSearching ? "检索中..." : "检索解决方案"}
                </button>
                <button
                  className="apple-button-secondary rounded-full px-4 py-2.5 text-sm"
                  type="button"
                  onClick={() => {
                    setDescription("");
                    setResult(null);
                    setErrorMessage("");
                    setDraftReplyText("");
                    setDraftReplyMeta(null);
                    setDraftReplyError("");
                    setCopyFeedback("");
                    setIsDraftModalOpen(false);
                  }}
                >
                  清空
                </button>
              </div>
            </div>

            {isConfigOpen && (
              <section className="apple-panel rounded-[24px] p-6">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <div className="apple-kicker">Configuration</div>
                    <h2 className="mt-3 text-xl font-semibold text-white">检索配置</h2>
                  </div>
                  <button
                    className="apple-button-primary rounded-full px-4 py-2 text-sm font-medium disabled:opacity-50"
                    type="button"
                    disabled={isSavingConfig}
                    onClick={() => void handleSaveConfig()}
                  >
                    {isSavingConfig ? "保存中..." : "保存配置"}
                  </button>
                </div>
                <div className="mt-5 grid gap-4">
                  <label className="grid gap-2 text-sm">
                    <span className="text-slate-400">本地 Jira 历史库</span>
                    <input
                      className="apple-input rounded-[18px] px-3 py-2.5"
                      value={sourceDbPath}
                      onChange={(event) => setSourceDbPath(event.target.value)}
                    />
                  </label>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <label className="grid gap-2 text-sm">
                      <span className="text-slate-400">建议复用阈值</span>
                      <input
                        className="apple-input rounded-[18px] px-3 py-2.5"
                        type="number"
                        step="0.01"
                        min="0"
                        max="1"
                        value={highThreshold}
                        onChange={(event) => setHighThreshold(Number(event.target.value))}
                      />
                    </label>
                    <label className="grid gap-2 text-sm">
                      <span className="text-slate-400">人工判断阈值</span>
                      <input
                        className="apple-input rounded-[18px] px-3 py-2.5"
                        type="number"
                        step="0.01"
                        min="0"
                        max="1"
                        value={mediumThreshold}
                        onChange={(event) => setMediumThreshold(Number(event.target.value))}
                      />
                    </label>
                  </div>
                  <label className="flex items-center justify-between gap-4 rounded-[20px] border border-white/10 bg-white/[0.03] px-4 py-3 text-sm">
                    <span>
                      <span className="block font-medium text-white">模型精判</span>
                      <span className="mt-1 block text-xs leading-5 text-slate-400">开启后会对边界候选调用当前模型，速度会变慢。</span>
                    </span>
                    <input
                      type="checkbox"
                      checked={modelReviewEnabled}
                      onChange={(event) => setModelReviewEnabled(event.target.checked)}
                    />
                  </label>
                  <div className="grid gap-2">
                    <div className="text-sm font-medium text-white">草稿生成与检索模型</div>
                    <div className="text-xs leading-6 text-slate-400">
                      这里的模型配置同时用于“生成回复草稿”；模型精判开关只控制检索边界复核。
                    </div>
                    <ModelSelector value={modelConfig} onChange={setModelConfig} />
                  </div>
                  {configNotice !== "" && <div className="apple-status-success rounded-[18px] px-4 py-3 text-sm">{configNotice}</div>}
                </div>
              </section>
            )}
          </section>

          <ResultPanel
            result={result}
            isDraftingReply={isDraftingReply}
            canDraftReply={canDraftReply}
            onDraftReply={() => void handleDraftReply()}
          />
        </div>
      </main>
      <DraftReplyModal
        isOpen={isDraftModalOpen}
        isGenerating={isDraftingReply}
        draftReplyText={draftReplyText}
        onDraftReplyTextChange={setDraftReplyText}
        onClose={() => setIsDraftModalOpen(false)}
        onCopy={() => void handleCopyDraft()}
        copyFeedback={copyFeedback}
        draftReplyMeta={draftReplyMeta}
        errorMessage={draftReplyError}
      />
    </div>
  );
}

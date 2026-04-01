"use client";

/**
 * Chat 工作区。
 *
 * 这里承载线程列表、消息流、SSE 事件展示、模型选择与 Skill 开关，
 * 是理解“聊天主链路”前端交互最直接的入口。
 */
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

import { createThread, getCatalog, getThread, listDocuments, listThreads, streamMessage } from "../lib/api";
import type {
  Catalog,
  ChatMessage,
  FinalResponse,
  KnowledgeDocument,
  ModelConfig,
  SseEvent,
  ThreadState,
  ThreadSummary,
  ToolEvent
} from "../lib/types";
import { ModelSelector } from "./model-selector";
import { useModelSettings } from "./model-settings-provider";

const DEFAULT_MODEL: ModelConfig = {
  mode: "learning",
  provider: "mock",
  model: "learning-mode",
  temperature: 0.2,
  max_tokens: 1024
};

const QUICK_PROMPTS = [
  { label: "报销计算", value: "报销 3 天 每天 100 含税" },
  { label: "总结重点", value: "请根据当前知识范围总结重点" },
  { label: "提取结论", value: "请提炼文档中的关键结论和行动建议" }
];

function formatDate(value?: string) {
  if (!value) return "-";
  return new Date(value).toLocaleString("zh-CN");
}

export function ChatWorkspace() {
  const { validateModelConfig, openModelSettings } = useModelSettings();
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [selectedThreadId, setSelectedThreadId] = useState("");
  const [threadState, setThreadState] = useState<ThreadState | null>(null);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [input, setInput] = useState("报销 3 天 每天 100 含税");
  const [error, setError] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [liveEvents, setLiveEvents] = useState<SseEvent[]>([]);
  const [liveToolEvents, setLiveToolEvents] = useState<ToolEvent[]>([]);
  const [liveFinal, setLiveFinal] = useState<FinalResponse | null>(null);
  const [modelConfig, setModelConfig] = useState<ModelConfig>(DEFAULT_MODEL);
  const [enabledSkills, setEnabledSkills] = useState<string[]>([]);
  const [isBootstrapping, setIsBootstrapping] = useState(true);
  const messageViewportRef = useRef<HTMLDivElement | null>(null);
  const modelValidation = validateModelConfig(modelConfig);

  async function bootstrap() {
    // 首次加载时同时拉取 catalog、线程列表和文档概览。
    // 这样你可以把 Chat 页面理解成“会话入口 + 学习面板”的组合。
    setIsBootstrapping(true);
    setError("");
    try {
      const [catalogData, threadsData, documentsData] = await Promise.all([getCatalog(), listThreads(), listDocuments()]);
      setCatalog(catalogData);
      setDocuments(documentsData);
      setThreads(threadsData);

      const defaultSkillIds = catalogData.skills.filter((skill) => skill.enabled_by_default).map((skill) => skill.id);
      if (enabledSkills.length === 0) {
        setEnabledSkills(defaultSkillIds);
      }

      if (threadsData.length > 0) {
        await selectThread(threadsData[0].thread_id);
        return;
      }

      const created = await createThread({ enabled_skills: defaultSkillIds });
      const nextThreads = await listThreads();
      setThreads(nextThreads);
      await selectThread(created.thread_id);
    } finally {
      setIsBootstrapping(false);
    }
  }

  useEffect(() => {
    void bootstrap().catch((cause) => setError(String(cause)));
  }, []);

  async function selectThread(threadId: string) {
    setError("");
    setSelectedThreadId(threadId);
    const detail = await getThread(threadId);
    setThreadState(detail);
    setModelConfig(detail.model_config);
    setEnabledSkills(detail.enabled_skills);
    setLiveToolEvents(detail.tool_events);
    setLiveFinal(detail.final_output);
    setLiveEvents([]);
  }

  useEffect(() => {
    const viewport = messageViewportRef.current;
    if (viewport == null) return;
    viewport.scrollTo({
      top: viewport.scrollHeight,
      behavior: isStreaming ? "smooth" : "auto"
    });
  }, [isStreaming, liveEvents.length, liveFinal, selectedThreadId, threadState?.messages.length]);

  async function handleCreateThread() {
    if (!modelValidation.isRunnable) {
      setError(modelValidation.message);
      openModelSettings(modelConfig.provider);
      return;
    }
    setError("");
    try {
      const created = await createThread({ enabled_skills: enabledSkills, model_config: modelConfig });
      const nextThreads = await listThreads();
      setThreads(nextThreads);
      await selectThread(created.thread_id);
    } catch (cause) {
      setError(String(cause));
    }
  }

  function applyEvent(event: SseEvent) {
    // 这是前端消费 SSE 的关键位置：
    // 后端每推来一个 route / tool / retrieval / final 事件，
    // 我们都即时写进本地状态，让执行过程可视化。
    setLiveEvents((current) => current.concat(event));
    if (event.event === "tool_start" || event.event === "tool_end") {
      const toolEvent = event.data as unknown as ToolEvent;
      setLiveToolEvents((current) => current.filter((item) => item.id !== toolEvent.id).concat(toolEvent));
    }
    if (event.event === "final") {
      setLiveFinal(event.data as unknown as FinalResponse);
    }
    if (event.event === "message") {
      const message = event.data as unknown as ChatMessage;
      setThreadState((current) =>
        current == null
          ? current
          : {
              ...current,
              messages: current.messages.concat(message)
            }
      );
    }
  }

  async function handleSend() {
    // 发送消息时，不是等后端全部跑完才更新 UI，
    // 而是边收事件边更新，这就是流式工作台的核心体验。
    if (isBootstrapping || selectedThreadId === "" || input.trim() === "") return;
    if (!modelValidation.isRunnable) {
      setError(modelValidation.message);
      openModelSettings(modelConfig.provider);
      return;
    }
    setError("");
    setIsStreaming(true);
    setLiveEvents([]);
    setLiveToolEvents([]);
    setLiveFinal(null);
    try {
      await streamMessage(
        selectedThreadId,
        {
          content: input,
          model_config: modelConfig,
          enabled_skills: enabledSkills
        },
        applyEvent
      );
      const [detail, threadList, docs] = await Promise.all([getThread(selectedThreadId), listThreads(), listDocuments()]);
      setThreadState(detail);
      setThreads(threadList);
      setDocuments(docs);
      setLiveToolEvents(detail.tool_events);
      setLiveFinal(detail.final_output);
      setInput("");
    } catch (cause) {
      setError(String(cause));
    } finally {
      setIsStreaming(false);
    }
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      void handleSend();
    }
  }

  return (
    <div className="grid min-h-full grid-cols-1 xl:h-full xl:min-h-0 xl:grid-cols-[300px_minmax(0,1fr)_360px]">
      <aside className="apple-sidebar border-b border-white/10 p-5 xl:flex xl:min-h-0 xl:flex-col xl:overflow-hidden xl:border-b-0 xl:border-r">
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="apple-kicker">Chat</div>
            <h2 className="mt-2 text-2xl font-semibold tracking-[-0.03em] text-white">会话线程</h2>
          </div>
          <button
            className="apple-button-secondary rounded-full px-4 py-2 text-sm"
            onClick={() => {
              void handleCreateThread();
            }}
            disabled={isBootstrapping}
          >
            新建
          </button>
        </div>

        <div className="apple-panel mt-5 rounded-[28px] p-4 text-sm leading-6 text-slate-300">
          Chat 保持多轮对话入口；知识树上传和 scoped 检索放到检索模式里。
          <div className="mt-3">
            <Link href="/retrieval" className="inline-flex items-center text-sky-200">
              去检索模式管理知识树
            </Link>
          </div>
        </div>

        <div className="mt-5 xl:min-h-0 xl:flex-1 xl:overflow-y-auto xl:pr-1">
          <div className="space-y-3">
            {isBootstrapping && threads.length === 0 && (
              <div className="apple-panel-subtle rounded-[24px] px-4 py-4 text-sm text-slate-400">正在加载线程...</div>
            )}
            {threads.map((thread) => (
              <button
                key={thread.thread_id}
                className={
                  "w-full rounded-[26px] p-4 text-left transition " +
                  (selectedThreadId === thread.thread_id ? "apple-nav-link apple-nav-link-active" : "apple-nav-link")
                }
                onClick={() => {
                  void selectThread(thread.thread_id);
                }}
              >
                <div className="font-medium text-white">{thread.title}</div>
                <div className="mt-2 line-clamp-2 text-sm leading-6 text-slate-400">{thread.last_message_preview || "还没有消息"}</div>
                <div className="mt-3 text-xs text-slate-500">{formatDate(thread.updated_at)}</div>
              </button>
            ))}
            {!isBootstrapping && threads.length === 0 && (
              <div className="apple-panel-subtle rounded-[24px] px-4 py-4 text-sm text-slate-400">
                还没有会话线程，点击右上角“新建”即可开始。
              </div>
            )}
            {error !== "" && !isBootstrapping && threads.length === 0 && (
              <button
                className="apple-button-secondary w-full rounded-[20px] px-4 py-3 text-sm"
                onClick={() => {
                  void bootstrap().catch((cause) => setError(String(cause)));
                }}
                type="button"
              >
                重新加载
              </button>
            )}
          </div>
        </div>
      </aside>

      <main className="flex min-w-0 flex-col xl:min-h-0">
        <header className="border-b border-white/10 px-6 py-5">
          <div className="mx-auto flex max-w-5xl items-end justify-between gap-4">
            <div>
              <div className="apple-kicker">Conversation</div>
              <div className="mt-2 text-2xl font-semibold tracking-[-0.03em] text-white">
                {threadState?.title ?? "正在加载..."}
              </div>
              <div className="mt-2 text-sm text-slate-400">会话式 Agent · Skill 路由 · 流式事件轨迹</div>
            </div>
            <div className="apple-pill hidden rounded-full px-3 py-1 text-xs xl:inline-flex">
              {isStreaming ? "实时处理中" : "等待输入"}
            </div>
          </div>
        </header>

        <section ref={messageViewportRef} className="flex-1 overflow-y-auto px-6 py-6">
          <div className="mx-auto flex max-w-5xl flex-col gap-4">
            {(threadState?.messages ?? []).map((message, index) => (
              <article
                key={(message.created_at ?? String(index)) + "-" + String(index)}
                className={
                  "max-w-3xl rounded-[28px] px-5 py-4 " +
                  (message.role === "human"
                    ? "self-end border border-sky-300/20 bg-[linear-gradient(180deg,rgba(10,132,255,0.26),rgba(10,132,255,0.14))] shadow-[0_24px_48px_-28px_rgba(10,132,255,0.85)]"
                    : "apple-panel self-start")
                }
              >
                <div className="mb-2 text-[11px] uppercase tracking-[0.24em] text-slate-400">{message.role}</div>
                <pre className="whitespace-pre-wrap text-sm leading-7 text-slate-50">{message.content}</pre>
              </article>
            ))}

            {liveFinal != null && (
              <section className="apple-status-success rounded-[30px] p-5">
                <div className="text-sm font-semibold text-emerald-100">FinalResponse</div>
                <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-slate-50">{liveFinal.answer}</p>
                {liveFinal.citations.length > 0 && (
                  <div className="mt-4 space-y-2">
                    <div className="text-xs uppercase tracking-[0.22em] text-slate-300">引用</div>
                    {liveFinal.citations.map((citation) => (
                      <div key={citation.chunk_id} className="apple-panel-subtle rounded-[22px] p-3 text-sm">
                        <div className="font-medium text-sky-200">{citation.document_name}</div>
                        <div className="mt-1 text-xs text-slate-500">{citation.tree_path || "/"}</div>
                        <div className="mt-2 leading-6 text-slate-300">{citation.snippet}</div>
                      </div>
                    ))}
                  </div>
                )}
              </section>
            )}
          </div>
        </section>

        <footer className="border-t border-white/10 p-6">
          <div className="apple-panel-strong mx-auto max-w-5xl rounded-[32px] p-4">
            <textarea
              className="apple-textarea min-h-32 w-full resize-none rounded-[24px] bg-transparent px-4 py-4 text-sm leading-7 outline-none"
              placeholder="输入任务，例如：报销 3 天 每天 100 含税；或：请根据文档总结重点"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleComposerKeyDown}
            />
            <div className="mt-4 flex flex-wrap gap-2">
              {QUICK_PROMPTS.map((item) => (
                <button
                  key={item.label}
                  className="apple-pill rounded-full px-3 py-1.5 text-xs text-slate-200 transition hover:bg-white/10"
                  onClick={() => setInput(item.value)}
                  type="button"
                >
                  {item.label}
                </button>
              ))}
            </div>
            <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="text-xs leading-6 text-slate-400">支持事件流、工具轨迹、RAG 引用与结构化输出；`Ctrl / ⌘ + Enter` 发送。</div>
              <button
                className="apple-button-primary rounded-full px-5 py-2.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50"
                onClick={() => {
                  void handleSend();
                }}
                disabled={isBootstrapping || isStreaming || selectedThreadId === "" || !modelValidation.isRunnable}
              >
                {isStreaming ? "执行中..." : "发送"}
              </button>
            </div>
          </div>
          {error !== "" && <div className="mx-auto mt-4 max-w-5xl text-sm text-rose-300">{error}</div>}
        </footer>
      </main>

      <aside className="apple-sidebar border-t border-white/10 p-5 xl:min-h-0 xl:overflow-y-auto xl:border-l xl:border-t-0">
        <section className="apple-panel rounded-[30px] p-4">
          <h2 className="text-base font-semibold text-white">模型配置</h2>
          <div className="mt-4">
            <ModelSelector value={modelConfig} onChange={setModelConfig} />
          </div>
        </section>

        <section className="apple-panel mt-5 rounded-[30px] p-4">
          <h2 className="text-base font-semibold text-white">Skill 开关</h2>
          <div className="mt-4 space-y-3">
            {catalog?.skills.map((skill) => (
              <label key={skill.id} className="apple-panel-subtle flex cursor-pointer items-start gap-3 rounded-[22px] p-3">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={enabledSkills.includes(skill.id)}
                  onChange={(event) => {
                    setEnabledSkills((current) =>
                      event.target.checked ? current.concat(skill.id) : current.filter((item) => item !== skill.id)
                    );
                  }}
                />
                <div>
                  <div className="font-medium text-white">{skill.name}</div>
                  <div className="mt-1 text-sm leading-6 text-slate-400">{skill.description}</div>
                </div>
              </label>
            ))}
          </div>
        </section>

        <section className="apple-panel mt-5 rounded-[30px] p-4">
          <h2 className="text-base font-semibold text-white">知识库概览</h2>
          <div className="mt-4 space-y-3 text-sm leading-6 text-slate-300">
            <div>当前文档数：{documents.length}</div>
            <div>可在检索模式里上传 `pdf / md / txt / doc / docx / xlsx`，并按树范围检索。</div>
            <Link href="/retrieval" className="inline-flex items-center text-sky-200">
              打开检索模式
            </Link>
          </div>
        </section>

        <section className="apple-panel mt-5 rounded-[30px] p-4">
          <h2 className="text-base font-semibold text-white">执行轨迹</h2>
          <div className="mt-4 space-y-3 text-sm">
            {liveToolEvents.length === 0 && <div className="text-slate-500">发送消息后会在这里看到 tool_start / tool_end。</div>}
            {liveToolEvents.map((event) => (
              <div key={event.id} className="apple-panel-subtle rounded-[22px] p-3">
                <div className="flex items-center justify-between">
                  <div className="font-medium text-white">{event.tool_name}</div>
                  <div className="text-xs text-slate-400">{event.status}</div>
                </div>
                <pre className="mt-2 whitespace-pre-wrap text-xs leading-6 text-slate-400">{JSON.stringify(event.input, null, 2)}</pre>
                {Object.keys(event.output ?? {}).length > 0 && (
                  <pre className="mt-2 whitespace-pre-wrap text-xs leading-6 text-emerald-300">{JSON.stringify(event.output, null, 2)}</pre>
                )}
              </div>
            ))}
          </div>
        </section>

        <section className="apple-panel mt-5 rounded-[30px] p-4">
          <h2 className="text-base font-semibold text-white">事件流</h2>
          <div className="mt-4 max-h-72 space-y-2 overflow-y-auto text-xs text-slate-400">
            {liveEvents.map((event, index) => (
              <div key={event.event + String(index)} className="apple-panel-subtle rounded-[20px] p-3">
                <div className="font-medium text-slate-200">{event.event}</div>
                <pre className="mt-2 whitespace-pre-wrap leading-6">{JSON.stringify(event.data, null, 2)}</pre>
              </div>
            ))}
          </div>
        </section>
      </aside>
    </div>
  );
}

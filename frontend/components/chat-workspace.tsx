"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

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
  const modelValidation = validateModelConfig(modelConfig);

  async function bootstrap() {
    // 首次加载时同时拉取 catalog、线程列表和文档概览。
    // 这样你可以把 Chat 页面理解成“会话入口 + 学习面板”的组合。
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
  }

  useEffect(() => {
    bootstrap().catch((cause) => setError(String(cause)));
  }, []);

  async function selectThread(threadId: string) {
    setSelectedThreadId(threadId);
    const detail = await getThread(threadId);
    setThreadState(detail);
    setModelConfig(detail.model_config);
    setEnabledSkills(detail.enabled_skills);
    setLiveToolEvents(detail.tool_events);
    setLiveFinal(detail.final_output);
    setLiveEvents([]);
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
    if (selectedThreadId === "" || input.trim() === "") return;
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

  return (
    <div className="grid min-h-screen grid-cols-[300px_minmax(0,1fr)_360px]">
      <aside className="border-r border-slate-800 bg-slate-900/50 p-5">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-xs uppercase tracking-[0.35em] text-sky-300">Chat</div>
            <h2 className="mt-2 text-xl font-semibold">会话线程</h2>
          </div>
          <button
            className="rounded-xl border border-slate-700 px-3 py-2 text-sm hover:border-sky-400"
            onClick={async () => {
              if (!modelValidation.isRunnable) {
                setError(modelValidation.message);
                openModelSettings(modelConfig.provider);
                return;
              }
              const created = await createThread({ enabled_skills: enabledSkills, model_config: modelConfig });
              const nextThreads = await listThreads();
              setThreads(nextThreads);
              await selectThread(created.thread_id);
            }}
          >
            新建
          </button>
        </div>

        <div className="mt-4 rounded-2xl border border-slate-800 bg-slate-950/50 p-4 text-sm text-slate-400">
          Chat 保持原有多轮对话入口；知识树上传和 scoped 检索放到检索模式里。
          <div className="mt-3">
            <Link href="/retrieval" className="text-amber-300">
              去检索模式管理知识树
            </Link>
          </div>
        </div>

        <div className="mt-5 space-y-3">
          {threads.map((thread) => (
            <button
              key={thread.thread_id}
              className={
                "w-full rounded-2xl border p-4 text-left transition " +
                (selectedThreadId === thread.thread_id
                  ? "border-sky-400 bg-slate-800"
                  : "border-slate-800 bg-slate-900 hover:border-slate-700")
              }
              onClick={() => {
                void selectThread(thread.thread_id);
              }}
            >
              <div className="font-medium">{thread.title}</div>
              <div className="mt-2 line-clamp-2 text-sm text-slate-400">{thread.last_message_preview || "还没有消息"}</div>
              <div className="mt-3 text-xs text-slate-500">{formatDate(thread.updated_at)}</div>
            </button>
          ))}
        </div>
      </aside>

      <main className="flex min-w-0 flex-col">
        <header className="border-b border-slate-800 px-6 py-5">
          <div className="text-sm text-slate-400">会话式 Agent · Skill 路由 · 流式事件轨迹</div>
          <div className="mt-1 text-lg font-semibold">{threadState?.title ?? "正在加载..."}</div>
        </header>

        <section className="flex-1 overflow-y-auto px-6 py-6">
          <div className="mx-auto flex max-w-4xl flex-col gap-4">
            {(threadState?.messages ?? []).map((message, index) => (
              <article
                key={(message.created_at ?? String(index)) + "-" + String(index)}
                className={
                  "max-w-3xl rounded-2xl border px-4 py-3 " +
                  (message.role === "human"
                    ? "self-end border-sky-500/40 bg-sky-500/10"
                    : "self-start border-slate-800 bg-slate-900")
                }
              >
                <div className="mb-2 text-xs uppercase tracking-wide text-slate-400">{message.role}</div>
                <pre className="whitespace-pre-wrap text-sm leading-6 text-slate-100">{message.content}</pre>
              </article>
            ))}

            {/* liveFinal 代表 final 事件已经到达，因此这里展示结构化最终输出。 */}
            {liveFinal != null && (
              <section className="rounded-3xl border border-emerald-500/30 bg-emerald-500/10 p-5">
                <div className="text-sm font-semibold text-emerald-300">FinalResponse</div>
                <p className="mt-3 whitespace-pre-wrap text-sm leading-6">{liveFinal.answer}</p>
                {liveFinal.citations.length > 0 && (
                  <div className="mt-4 space-y-2">
                    <div className="text-xs uppercase tracking-wide text-slate-300">引用</div>
                    {liveFinal.citations.map((citation) => (
                      <div key={citation.chunk_id} className="rounded-xl border border-slate-700 bg-slate-950/50 p-3 text-sm">
                        <div className="font-medium text-sky-300">{citation.document_name}</div>
                        <div className="mt-1 text-xs text-slate-500">{citation.tree_path || "/"}</div>
                        <div className="mt-2 text-slate-300">{citation.snippet}</div>
                      </div>
                    ))}
                  </div>
                )}
              </section>
            )}
          </div>
        </section>

        <footer className="border-t border-slate-800 p-6">
          <div className="mx-auto max-w-4xl rounded-3xl border border-slate-800 bg-slate-900 p-4">
            <textarea
              className="min-h-32 w-full resize-none bg-transparent text-sm leading-6 outline-none"
              placeholder="输入任务，例如：报销 3 天 每天 100 含税；或：请根据文档总结重点"
              value={input}
              onChange={(event) => setInput(event.target.value)}
            />
            <div className="mt-4 flex items-center justify-between">
              <div className="text-xs text-slate-500">当前支持事件流、工具轨迹、RAG 引用与结构化输出。</div>
              <button
                className="rounded-xl bg-sky-500 px-4 py-2 text-sm font-medium text-slate-950 hover:bg-sky-400 disabled:cursor-not-allowed disabled:bg-slate-700"
                onClick={() => {
                  void handleSend();
                }}
                disabled={isStreaming || selectedThreadId === "" || !modelValidation.isRunnable}
              >
                {isStreaming ? "执行中..." : "发送"}
              </button>
            </div>
          </div>
          {error !== "" && <div className="mx-auto mt-4 max-w-4xl text-sm text-rose-300">{error}</div>}
        </footer>
      </main>

      <aside className="border-l border-slate-800 bg-slate-900/60 p-5">
        <section className="rounded-3xl border border-slate-800 bg-slate-900 p-4">
          <h2 className="text-base font-semibold">模型配置</h2>
          <div className="mt-4">
            <ModelSelector value={modelConfig} onChange={setModelConfig} />
          </div>
        </section>

        <section className="mt-5 rounded-3xl border border-slate-800 bg-slate-900 p-4">
          <h2 className="text-base font-semibold">Skill 开关</h2>
          <div className="mt-4 space-y-3">
            {catalog?.skills.map((skill) => (
              <label key={skill.id} className="flex cursor-pointer items-start gap-3 rounded-2xl border border-slate-800 p-3">
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
                  <div className="font-medium">{skill.name}</div>
                  <div className="mt-1 text-sm text-slate-400">{skill.description}</div>
                </div>
              </label>
            ))}
          </div>
        </section>

        <section className="mt-5 rounded-3xl border border-slate-800 bg-slate-900 p-4">
          <h2 className="text-base font-semibold">知识库概览</h2>
          <div className="mt-4 space-y-3 text-sm text-slate-400">
            <div>当前文档数：{documents.length}</div>
            <div>可在检索模式里上传 `pdf / md / txt / doc / docx / xlsx`，并按树范围检索。</div>
            <Link href="/retrieval" className="inline-block text-amber-300">
              打开检索模式
            </Link>
          </div>
        </section>

        <section className="mt-5 rounded-3xl border border-slate-800 bg-slate-900 p-4">
          <h2 className="text-base font-semibold">执行轨迹</h2>
          <div className="mt-4 space-y-3 text-sm">
            {liveToolEvents.length === 0 && <div className="text-slate-500">发送消息后会在这里看到 tool_start / tool_end。</div>}
            {liveToolEvents.map((event) => (
              <div key={event.id} className="rounded-2xl border border-slate-800 bg-slate-950/60 p-3">
                <div className="flex items-center justify-between">
                  <div className="font-medium">{event.tool_name}</div>
                  <div className="text-xs text-slate-400">{event.status}</div>
                </div>
                <pre className="mt-2 whitespace-pre-wrap text-xs text-slate-400">{JSON.stringify(event.input, null, 2)}</pre>
                {Object.keys(event.output ?? {}).length > 0 && (
                  <pre className="mt-2 whitespace-pre-wrap text-xs text-emerald-300">{JSON.stringify(event.output, null, 2)}</pre>
                )}
              </div>
            ))}
          </div>
        </section>

        <section className="mt-5 rounded-3xl border border-slate-800 bg-slate-900 p-4">
          <h2 className="text-base font-semibold">事件流</h2>
          <div className="mt-4 max-h-72 space-y-2 overflow-y-auto text-xs text-slate-400">
            {liveEvents.map((event, index) => (
              <div key={event.event + String(index)} className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
                <div className="font-medium text-slate-200">{event.event}</div>
                <pre className="mt-2 whitespace-pre-wrap">{JSON.stringify(event.data, null, 2)}</pre>
              </div>
            ))}
          </div>
        </section>
      </aside>
    </div>
  );
}

"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { createThread, getCatalog, getThread, listDocuments, listThreads, streamMessage, uploadDocument } from "../lib/api";
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

function formatDate(value?: string) {
  if (!value) return "-";
  return new Date(value).toLocaleString("zh-CN");
}

const DEFAULT_MODEL: ModelConfig = {
  provider: "mock",
  model: "learning-mode",
  temperature: 0.2,
  max_tokens: 1024
};

export function ChatWorkspace() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [selectedThreadId, setSelectedThreadId] = useState<string>("");
  const [threadState, setThreadState] = useState<ThreadState | null>(null);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [input, setInput] = useState("报销 3 天 每天 100 含税");
  const [error, setError] = useState<string>("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [liveEvents, setLiveEvents] = useState<SseEvent[]>([]);
  const [liveToolEvents, setLiveToolEvents] = useState<ToolEvent[]>([]);
  const [liveFinal, setLiveFinal] = useState<FinalResponse | null>(null);
  const [modelConfig, setModelConfig] = useState<ModelConfig>(DEFAULT_MODEL);
  const [enabledSkills, setEnabledSkills] = useState<string[]>([]);

  const mergedMessages = useMemo<ChatMessage[]>(() => {
    if (!threadState) return [];
    return threadState.messages;
  }, [threadState]);

  async function bootstrap() {
    const [catalogData, threadsData, documentsData] = await Promise.all([
      getCatalog(),
      listThreads(),
      listDocuments()
    ]);
    setCatalog(catalogData);
    setDocuments(documentsData);
    setThreads(threadsData);

    const defaultSkillIds = catalogData.skills.filter((skill) => skill.enabled_by_default).map((skill) => skill.id);
    setEnabledSkills(defaultSkillIds);

    if (threadsData.length > 0) {
      await selectThread(threadsData[0].thread_id);
    } else {
      const created = await createThread({ enabled_skills: defaultSkillIds });
      const nextThreads = await listThreads();
      setThreads(nextThreads);
      await selectThread(created.thread_id);
    }
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
    setLiveEvents((current) => [...current, event]);
    if (event.event === "tool_start" || event.event === "tool_end") {
      const toolEvent = event.data as unknown as ToolEvent;
      setLiveToolEvents((current) => {
        const filtered = current.filter((item) => item.id !== toolEvent.id);
        return [...filtered, toolEvent];
      });
    }
    if (event.event === "final") {
      setLiveFinal(event.data as unknown as FinalResponse);
    }
    if (event.event === "message") {
      const message = event.data as unknown as ChatMessage;
      setThreadState((current) =>
        current
          ? {
              ...current,
              messages: [...current.messages, message]
            }
          : current
      );
    }
  }

  async function handleSend() {
    if (!selectedThreadId || !input.trim()) return;
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
      const [detail, threadList] = await Promise.all([getThread(selectedThreadId), listThreads()]);
      setThreadState(detail);
      setThreads(threadList);
      setLiveToolEvents(detail.tool_events);
      setLiveFinal(detail.final_output);
      setInput("");
    } catch (cause) {
      setError(String(cause));
    } finally {
      setIsStreaming(false);
    }
  }

  async function handleUpload(file: File | null) {
    if (!file) return;
    setError("");
    try {
      await uploadDocument(file);
      setDocuments(await listDocuments());
    } catch (cause) {
      setError(String(cause));
    }
  }

  return (
    <div className="grid min-h-screen grid-cols-[280px_minmax(0,1fr)_360px] bg-slate-950 text-slate-100">
      <aside className="border-r border-slate-800 bg-slate-900/70 p-5">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <div className="text-xs uppercase tracking-[0.3em] text-sky-300">LangChain</div>
            <h1 className="mt-2 text-xl font-semibold">Learning Demo</h1>
          </div>
          <button
            className="rounded-lg border border-slate-700 px-3 py-2 text-sm hover:border-sky-400"
            onClick={async () => {
              const created = await createThread({ enabled_skills: enabledSkills });
              const nextThreads = await listThreads();
              setThreads(nextThreads);
              await selectThread(created.thread_id);
            }}
          >
            新建
          </button>
        </div>

        <Link href="/catalog" className="mb-4 inline-block text-sm text-slate-300">
          查看 Skill / Tool Catalog →
        </Link>

        <div className="space-y-3">
          {threads.map((thread) => (
            <button
              key={thread.thread_id}
              className={`w-full rounded-2xl border p-4 text-left transition ${
                selectedThreadId === thread.thread_id
                  ? "border-sky-400 bg-slate-800"
                  : "border-slate-800 bg-slate-900 hover:border-slate-700"
              }`}
              onClick={() => selectThread(thread.thread_id)}
            >
              <div className="font-medium">{thread.title}</div>
              <div className="mt-2 line-clamp-2 text-sm text-slate-400">{thread.last_message_preview || "还没有消息"}</div>
              <div className="mt-3 text-xs text-slate-500">{formatDate(thread.updated_at)}</div>
            </button>
          ))}
        </div>
      </aside>

      <main className="flex min-w-0 flex-col">
        <header className="border-b border-slate-800 px-6 py-4">
          <div className="text-sm text-slate-400">会话式 Agent · RAG · Skill · Streaming</div>
          <div className="mt-1 text-lg font-semibold">{threadState?.title ?? "正在加载..."}</div>
        </header>

        <section className="flex-1 overflow-y-auto px-6 py-6">
          <div className="mx-auto flex max-w-4xl flex-col gap-4">
            {mergedMessages.map((message, index) => (
              <article
                key={`${message.created_at ?? index}-${index}`}
                className={`max-w-3xl rounded-2xl border px-4 py-3 ${
                  message.role === "human"
                    ? "self-end border-sky-500/40 bg-sky-500/10"
                    : "self-start border-slate-800 bg-slate-900"
                }`}
              >
                <div className="mb-2 text-xs uppercase tracking-wide text-slate-400">{message.role}</div>
                <pre className="whitespace-pre-wrap text-sm leading-6 text-slate-100">{message.content}</pre>
              </article>
            ))}

            {liveFinal && (
              <section className="rounded-3xl border border-emerald-500/30 bg-emerald-500/10 p-5">
                <div className="text-sm font-semibold text-emerald-300">FinalResponse</div>
                <p className="mt-3 whitespace-pre-wrap text-sm leading-6">{liveFinal.answer}</p>
                {liveFinal.citations.length > 0 && (
                  <div className="mt-4 space-y-2">
                    <div className="text-xs uppercase tracking-wide text-slate-300">引用</div>
                    {liveFinal.citations.map((citation) => (
                      <div key={citation.chunk_id} className="rounded-xl border border-slate-700 bg-slate-950/50 p-3 text-sm">
                        <div className="font-medium text-sky-300">{citation.document_name}</div>
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
                onClick={handleSend}
                disabled={isStreaming || !selectedThreadId}
              >
                {isStreaming ? "执行中..." : "发送"}
              </button>
            </div>
          </div>
          {error && <div className="mx-auto mt-4 max-w-4xl text-sm text-rose-300">{error}</div>}
        </footer>
      </main>

      <aside className="border-l border-slate-800 bg-slate-900/60 p-5">
        <section className="rounded-3xl border border-slate-800 bg-slate-900 p-4">
          <h2 className="text-base font-semibold">模型配置</h2>
          <div className="mt-4 grid gap-3">
            <label className="grid gap-1 text-sm">
              <span className="text-slate-400">Provider</span>
              <input
                className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                value={modelConfig.provider}
                onChange={(event) => setModelConfig((current) => ({ ...current, provider: event.target.value }))}
              />
            </label>
            <label className="grid gap-1 text-sm">
              <span className="text-slate-400">Model</span>
              <input
                className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                value={modelConfig.model}
                onChange={(event) => setModelConfig((current) => ({ ...current, model: event.target.value }))}
              />
            </label>
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
                      event.target.checked ? [...current, skill.id] : current.filter((item) => item !== skill.id)
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
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold">知识库</h2>
            <label className="cursor-pointer rounded-lg border border-slate-700 px-3 py-1 text-xs text-slate-300">
              上传
              <input
                type="file"
                className="hidden"
                accept=".txt,.md,.pdf,.docx"
                onChange={(event) => void handleUpload(event.target.files?.[0] ?? null)}
              />
            </label>
          </div>
          <div className="mt-4 space-y-3">
            {documents.length === 0 && <div className="text-sm text-slate-500">还没有文档，上传后可体验 RAG。</div>}
            {documents.map((document) => (
              <div key={document.id} className="rounded-2xl border border-slate-800 p-3">
                <div className="font-medium">{document.name}</div>
                <div className="mt-2 text-xs text-slate-400">
                  {document.status} · chunks {document.chunk_count} · {formatDate(document.created_at)}
                </div>
                {document.error_message && <div className="mt-2 text-xs text-rose-300">{document.error_message}</div>}
              </div>
            ))}
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
          <div className="mt-4 max-h-64 space-y-2 overflow-y-auto text-xs text-slate-400">
            {liveEvents.map((event, index) => (
              <div key={`${event.event}-${index}`} className="rounded-xl border border-slate-800 p-2">
                <div className="font-medium text-sky-300">{event.event}</div>
                <pre className="mt-1 whitespace-pre-wrap">{JSON.stringify(event.data, null, 2)}</pre>
              </div>
            ))}
          </div>
        </section>
      </aside>
    </div>
  );
}

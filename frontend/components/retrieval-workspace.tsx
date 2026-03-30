"use client";

import { useEffect, useRef, useState } from "react";

import {
  createKnowledgeNode,
  deleteKnowledgeDocument,
  deleteKnowledgeNode,
  getKnowledgeNodeDetail,
  getKnowledgeTree,
  queryRetrieval,
  updateKnowledgeDocument,
  uploadDirectory,
  uploadDocumentToNode
} from "../lib/api";
import type {
  KnowledgeTreeNodeDetail,
  KnowledgeTreeResponse,
  ModelConfig,
  RetrievalResult,
  ScopeType
} from "../lib/types";
import { KnowledgeTree } from "./knowledge-tree";
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
  if (value == null || value === "") return "-";
  return new Date(value).toLocaleString("zh-CN");
}

export function RetrievalWorkspace() {
  const { validateModelConfig, openModelSettings } = useModelSettings();
  const [tree, setTree] = useState<KnowledgeTreeResponse | null>(null);
  const [detail, setDetail] = useState<KnowledgeTreeNodeDetail | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState("root");
  const [scopeType, setScopeType] = useState<ScopeType>("global");
  const [query, setQuery] = useState("请总结这个范围里的重点内容");
  const [result, setResult] = useState<RetrievalResult | null>(null);
  const [newNodeName, setNewNodeName] = useState("");
  const [modelConfig, setModelConfig] = useState<ModelConfig>(DEFAULT_MODEL);
  const [linkDrafts, setLinkDrafts] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [savingDocumentId, setSavingDocumentId] = useState("");
  const directoryInputRef = useRef<HTMLInputElement | null>(null);
  const modelValidation = validateModelConfig(modelConfig);

  async function refreshTree() {
    // 检索模式的左侧树是整个页面的状态基座。
    // 目录上传、建节点、单文件上传后，都会回到这里刷新。
    const nextTree = await getKnowledgeTree();
    setTree(nextTree);
    return nextTree;
  }

  async function refreshDetail(nodeId: string) {
    const nextDetail = await getKnowledgeNodeDetail(nodeId);
    setDetail(nextDetail);
    return nextDetail;
  }

  useEffect(() => {
    refreshTree().catch((cause) => setError(String(cause)));
  }, []);

  useEffect(() => {
    if (directoryInputRef.current == null) return;
    directoryInputRef.current.setAttribute("webkitdirectory", "");
    directoryInputRef.current.setAttribute("directory", "");
  }, []);

  useEffect(() => {
    getKnowledgeNodeDetail(selectedNodeId)
      .then(setDetail)
      .catch((cause) => setError(String(cause)));
  }, [selectedNodeId, tree?.root.children_count, tree?.root.document_count]);

  useEffect(() => {
    const nextDrafts: Record<string, string> = {};
    for (const document of detail?.documents ?? []) {
      nextDrafts[document.id] = document.external_url ?? "";
    }
    setLinkDrafts(nextDrafts);
  }, [detail]);

  async function handleCreateNode() {
    if (newNodeName.trim() === "") return;
    setError("");
    setIsBusy(true);
    try {
      await createKnowledgeNode({ name: newNodeName.trim(), parent_id: selectedNodeId });
      setNewNodeName("");
      await refreshTree();
      await refreshDetail(selectedNodeId);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setIsBusy(false);
    }
  }

  async function handleDirectoryUpload(files: FileList | null) {
    if (files == null || files.length === 0) return;
    setError("");
    setIsBusy(true);
    try {
      await uploadDirectory(Array.from(files), selectedNodeId === "root" ? null : selectedNodeId);
      await refreshTree();
      await refreshDetail(selectedNodeId);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setIsBusy(false);
    }
  }

  async function handleSingleFileUpload(file: File | null) {
    if (file == null) return;
    setError("");
    setIsBusy(true);
    try {
      await uploadDocumentToNode(selectedNodeId, file);
      await refreshTree();
      await refreshDetail(selectedNodeId);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setIsBusy(false);
    }
  }

  async function handleDeleteCurrentNode() {
    if (detail == null || detail.node.id === "root") return;
    const confirmed = window.confirm(
      `确认删除目录「${detail.node.name}」吗？\n这会同时删除 ${detail.recursive_children_count} 个子目录和 ${detail.recursive_document_count} 个文件。`
    );
    if (!confirmed) return;
    setError("");
    setIsBusy(true);
    try {
      const fallbackNodeId = detail.node.parent_id ?? "root";
      await deleteKnowledgeNode(detail.node.id);
      setResult(null);
      setSelectedNodeId(fallbackNodeId);
      await refreshTree();
      await refreshDetail(fallbackNodeId);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setIsBusy(false);
    }
  }

  async function handleDeleteDocument(documentId: string, documentName: string) {
    const confirmed = window.confirm(`确认删除文件「${documentName}」吗？`);
    if (!confirmed) return;
    setError("");
    setIsBusy(true);
    try {
      await deleteKnowledgeDocument(documentId);
      await refreshTree();
      await refreshDetail(selectedNodeId);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setIsBusy(false);
    }
  }

  async function handleSaveDocumentLink(documentId: string) {
    setError("");
    setSavingDocumentId(documentId);
    try {
      await updateKnowledgeDocument(documentId, { external_url: linkDrafts[documentId] ?? "" });
      await refreshDetail(selectedNodeId);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setSavingDocumentId("");
    }
  }

  async function handleQuery() {
    // 这里走的是“单次 scoped RAG”链路：
    // 当前问题 + scope + model config -> /api/retrieval/query -> summary/citations/context
    if (query.trim() === "") return;
    if (!modelValidation.isRunnable) {
      setError(modelValidation.message);
      openModelSettings(modelConfig.provider);
      return;
    }
    setError("");
    setIsBusy(true);
    try {
      const nextResult = await queryRetrieval({
        query,
        scope_type: scopeType,
        scope_id: scopeType === "tree_recursive" ? selectedNodeId : null,
        model_config: modelConfig
      });
      setResult(nextResult);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen grid-cols-[340px_minmax(0,1fr)]">
      <aside className="border-r border-slate-800 bg-slate-900/50 p-5">
        <div>
          <div className="text-xs uppercase tracking-[0.35em] text-amber-300">检索模式</div>
          <h2 className="mt-2 text-xl font-semibold">知识树</h2>
          <p className="mt-3 text-sm leading-6 text-slate-400">支持目录上传保留相对路径，也支持手动建节点后继续补文件。</p>
        </div>

        <div className="mt-5 rounded-2xl border border-slate-800 bg-slate-950/50 p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="text-sm font-medium text-slate-200">当前节点</div>
            {selectedNodeId !== "root" && (
              <button
                className="rounded-lg border border-rose-400/40 px-2.5 py-1 text-xs text-rose-200 hover:bg-rose-400/10 disabled:border-slate-700 disabled:text-slate-500"
                onClick={() => {
                  void handleDeleteCurrentNode();
                }}
                disabled={isBusy}
              >
                删除目录
              </button>
            )}
          </div>
          <div className="mt-2 text-sm text-slate-400">{detail?.node.name ?? "全部知识"}</div>
          <div className="mt-2 text-xs text-slate-500">路径：{detail?.node.path ?? "/"}</div>
          <div className="mt-3 grid gap-2 text-xs text-slate-400">
            <div>直属文件：{detail?.documents.length ?? 0}</div>
            <div>递归文件：{detail?.recursive_document_count ?? 0}</div>
            <div>子节点：{detail?.recursive_children_count ?? 0}</div>
          </div>
        </div>

        <div className="mt-5 rounded-2xl border border-slate-800 bg-slate-950/50 p-4">
          <div className="text-sm font-medium text-slate-200">新建节点</div>
          <input
            className="mt-3 w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm"
            value={newNodeName}
            onChange={(event) => setNewNodeName(event.target.value)}
            placeholder="例如：项目周报"
          />
          <button
            className="mt-3 w-full rounded-xl border border-amber-300/50 px-3 py-2 text-sm text-amber-200 hover:bg-amber-300/10"
            onClick={() => {
              void handleCreateNode();
            }}
            disabled={isBusy}
          >
            在当前节点下创建
          </button>
        </div>

        <div className="mt-5 rounded-2xl border border-slate-800 bg-slate-950/50 p-4">
          <div className="text-sm font-medium text-slate-200">上传</div>
          <div className="mt-3 grid gap-3">
            <label className="rounded-xl border border-slate-700 px-3 py-2 text-sm text-slate-300">
              上传目录
              <input
                ref={directoryInputRef}
                type="file"
                className="hidden"
                multiple
                onChange={(event) => {
                  void handleDirectoryUpload(event.target.files);
                }}
              />
            </label>
            <label className="rounded-xl border border-slate-700 px-3 py-2 text-sm text-slate-300">
              上传到当前节点
              <input
                type="file"
                className="hidden"
                accept=".txt,.md,.pdf,.doc,.docx,.xlsx"
                onChange={(event) => {
                  void handleSingleFileUpload(event.target.files?.[0] ?? null);
                }}
              />
            </label>
          </div>
          <div className="mt-3 text-xs leading-6 text-slate-500">支持 `pdf / md / txt / doc / docx / xlsx`。目录上传会按相对路径自动建树。</div>
        </div>

        <div className="mt-5 max-h-[calc(100vh-380px)] overflow-y-auto rounded-2xl border border-slate-800 bg-slate-950/30 p-3">
          {tree != null && <KnowledgeTree node={tree.root} selectedId={selectedNodeId} onSelect={setSelectedNodeId} />}
        </div>
      </aside>

      <main className="min-w-0 px-6 py-6">
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
          <section className="rounded-3xl border border-slate-800 bg-slate-900 p-5">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm text-slate-400">单次 scoped RAG 检索 + LLM 汇总</div>
                <h3 className="mt-1 text-xl font-semibold">检索表单</h3>
              </div>
              <div className="text-xs text-slate-500">当前节点：{detail?.node.name ?? "全部知识"}</div>
            </div>

            <div className="mt-5 grid gap-4 lg:grid-cols-2">
              <label className="grid gap-1 text-sm">
                <span className="text-slate-400">Scope</span>
                <select
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                  value={scopeType}
                  onChange={(event) => setScopeType(event.target.value as ScopeType)}
                >
                  <option value="global">全部文件</option>
                  <option value="tree_recursive">当前节点递归范围</option>
                </select>
              </label>
              <div className="lg:col-span-2">
                <ModelSelector value={modelConfig} onChange={setModelConfig} />
              </div>
            </div>

            <textarea
              className="mt-5 min-h-32 w-full rounded-2xl border border-slate-800 bg-slate-950/60 px-4 py-3 text-sm leading-6 outline-none"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />

            <div className="mt-4 flex items-center justify-between">
              <div className="text-xs text-slate-500">
                {scopeType === "global" ? "会跨全部文档检索" : "只会检索当前节点及其全部子节点"}
              </div>
              <button
                className="rounded-xl bg-amber-300 px-4 py-2 text-sm font-medium text-slate-950 hover:bg-amber-200 disabled:bg-slate-700"
                onClick={() => {
                  void handleQuery();
                }}
                disabled={isBusy || !modelValidation.isRunnable}
              >
                {isBusy ? "处理中..." : "开始检索"}
              </button>
            </div>

            {result != null && (
              <div className="mt-6 space-y-5">
                <div className="rounded-2xl border border-emerald-400/30 bg-emerald-400/10 p-4">
                  <div className="text-sm font-medium text-emerald-200">LLM 汇总</div>
                  <div className="mt-3 whitespace-pre-wrap text-sm leading-6">{result.summary}</div>
                </div>

                {result.related_document_links.length > 0 && (
                  <div className="rounded-2xl border border-sky-400/30 bg-sky-400/10 p-4">
                    <div className="text-sm font-medium text-sky-200">相关文档链接</div>
                    <div className="mt-3 space-y-2">
                      {result.related_document_links.map((link) => (
                        <a
                          key={link.document_id + link.external_url}
                          className="block text-sm text-sky-100 underline decoration-sky-300/50 underline-offset-4 hover:text-white"
                          href={link.external_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {link.document_name}
                        </a>
                      ))}
                    </div>
                  </div>
                )}

                <div className="rounded-2xl border border-slate-800 bg-slate-950/50 p-4">
                  <div className="text-sm font-medium text-slate-200">检索上下文</div>
                  <pre className="mt-3 whitespace-pre-wrap text-xs leading-6 text-slate-400">{result.retrieval_context}</pre>
                </div>

                <div className="space-y-3">
                  <div className="text-sm font-medium text-slate-200">引用片段</div>
                  {result.citations.map((citation) => (
                    <div key={citation.chunk_id} className="rounded-2xl border border-slate-800 bg-slate-950/50 p-4">
                      <div className="flex items-center justify-between gap-3">
                        <div className="font-medium text-amber-200">{citation.document_name}</div>
                        <div className="text-xs text-slate-500">{citation.tree_path || "/"}</div>
                      </div>
                      <div className="mt-2 text-xs text-slate-500">{citation.relative_path || citation.document_name}</div>
                      <div className="mt-3 whitespace-pre-wrap text-sm text-slate-300">{citation.snippet}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>

          <section className="rounded-3xl border border-slate-800 bg-slate-900 p-5">
            <h3 className="text-lg font-semibold">当前节点详情</h3>
            <div className="mt-4 space-y-3 text-sm text-slate-400">
              <div>名称：{detail?.node.name ?? "-"}</div>
              <div>路径：{detail?.node.path ?? "/"}</div>
              <div>直属子节点：{detail?.children.length ?? 0}</div>
              <div>直属文件：{detail?.documents.length ?? 0}</div>
            </div>

            <div className="mt-5">
              <div className="text-sm font-medium text-slate-200">直属文件</div>
              <div className="mt-3 space-y-3">
                {(detail?.documents ?? []).map((document) => (
                  <div key={document.id} className="rounded-2xl border border-slate-800 bg-slate-950/50 p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="font-medium text-slate-200">{document.name}</div>
                      <button
                        className="rounded-lg border border-rose-400/40 px-2.5 py-1 text-xs text-rose-200 hover:bg-rose-400/10 disabled:border-slate-700 disabled:text-slate-500"
                        onClick={() => {
                          void handleDeleteDocument(document.id, document.name);
                        }}
                        disabled={isBusy}
                      >
                        删除
                      </button>
                    </div>
                    <div className="mt-2 text-xs text-slate-500">
                      {document.type} · {document.status} · chunks {document.chunk_count}
                    </div>
                    <div className="mt-1 text-xs text-slate-500">{document.relative_path}</div>
                    <div className="mt-1 text-xs text-slate-500">{formatDate(document.created_at)}</div>
                    <div className="mt-3 grid gap-2">
                      <input
                        className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100 outline-none placeholder:text-slate-600"
                        value={linkDrafts[document.id] ?? ""}
                        onChange={(event) =>
                          setLinkDrafts((current) => ({ ...current, [document.id]: event.target.value }))
                        }
                        placeholder="给这个文档配置线上链接，支持问题 Agent 命中后会回写到表格"
                      />
                      <button
                        className="rounded-xl border border-sky-400/40 px-3 py-2 text-xs text-sky-200 hover:bg-sky-400/10 disabled:border-slate-700 disabled:text-slate-500"
                        onClick={() => {
                          void handleSaveDocumentLink(document.id);
                        }}
                        disabled={savingDocumentId === document.id}
                      >
                        {savingDocumentId === document.id ? "保存中..." : "保存文档链接"}
                      </button>
                    </div>
                  </div>
                ))}
                {(detail?.documents.length ?? 0) === 0 && <div className="text-sm text-slate-500">当前节点下还没有直属文件。</div>}
              </div>
            </div>
          </section>
        </div>

        {error !== "" && <div className="mt-6 text-sm text-rose-300">{error}</div>}
      </main>
    </div>
  );
}

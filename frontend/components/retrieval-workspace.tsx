"use client";

/**
 * 检索模式工作区。
 *
 * 这里把“知识树管理”和“单次 scoped RAG 查询”放到同一个页面：
 * 先决定知识范围，再上传文档、维护链接、发起检索，最后查看 citation 与总结结果。
 */
import { useEffect, useRef, useState } from "react";

import {
  createKnowledgeNode,
  deleteKnowledgeDocument,
  deleteKnowledgeNode,
  getKnowledgeNodeDetail,
  getKnowledgeTree,
  importGitLabTree,
  queryRetrieval,
  updateKnowledgeDocument,
  uploadDirectory,
  uploadDocumentToNode
} from "../lib/api";
import type {
  GitLabTreeImportResponse,
  KnowledgeTreeNodeDetail,
  KnowledgeTreeResponse,
  ModelConfig,
  RetrievalResult,
  ScopeType
} from "../lib/types";
import { KnowledgeTree } from "./knowledge-tree";
import { ModelSelector } from "./model-selector";
import { useModelSettings } from "./model-settings-provider";

/** 检索模式默认使用学习模式模型，方便项目开箱即用。 */
const DEFAULT_MODEL: ModelConfig = {
  mode: "learning",
  provider: "mock",
  model: "learning-mode",
  temperature: 0.2,
  max_tokens: 1024
};

/** 把服务端时间戳格式化成中文可读时间。 */
function formatDate(value?: string) {
  if (value == null || value === "") return "-";
  return new Date(value).toLocaleString("zh-CN");
}

export function RetrievalWorkspace() {
  const { validateModelConfig, openModelSettings } = useModelSettings();
  // 页面状态可以粗分成三类：
  // 1. 左侧知识树状态；2. 中间节点详情状态；3. 右侧查询与结果状态。
  const [tree, setTree] = useState<KnowledgeTreeResponse | null>(null);
  const [detail, setDetail] = useState<KnowledgeTreeNodeDetail | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState("root");
  const [scopeType, setScopeType] = useState<ScopeType>("global");
  const [query, setQuery] = useState("请总结这个范围里的重点内容");
  const [result, setResult] = useState<RetrievalResult | null>(null);
  const [newNodeName, setNewNodeName] = useState("");
  const [gitlabTreeUrl, setGitlabTreeUrl] = useState("");
  const [gitlabImportResult, setGitlabImportResult] = useState<GitLabTreeImportResponse | null>(null);
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
    // `webkitdirectory` 不是标准 React 属性，因此这里在挂载后手动补上，
    // 让浏览器文件选择框支持“选择整个目录”。
    if (directoryInputRef.current == null) return;
    directoryInputRef.current.setAttribute("webkitdirectory", "");
    directoryInputRef.current.setAttribute("directory", "");
  }, []);

  useEffect(() => {
    // 当前选中的节点、或树的节点/文件数量发生变化后，都重新加载右侧详情。
    // 这样创建节点、上传文件、删除节点后，详情区会自动同步最新内容。
    getKnowledgeNodeDetail(selectedNodeId)
      .then(setDetail)
      .catch((cause) => setError(String(cause)));
  }, [selectedNodeId, tree?.root.children_count, tree?.root.document_count]);

  useEffect(() => {
    // 文档外链编辑使用单独草稿，而不是直接修改 detail，
    // 这样用户在输入时不会污染刚从后端拿到的原始对象。
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

  async function handleGitLabImport() {
    if (gitlabTreeUrl.trim() === "") return;
    setError("");
    setIsBusy(true);
    try {
      const result = await importGitLabTree({
        tree_url: gitlabTreeUrl.trim(),
        parent_node_id: selectedNodeId === "root" ? null : selectedNodeId
      });
      setGitlabImportResult(result);
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
    <div className="grid min-h-full grid-cols-1 xl:h-full xl:min-h-0 xl:grid-cols-[340px_minmax(0,1fr)]">
      {/* 左侧：知识树与节点级操作入口。 */}
      <aside className="apple-sidebar border-b border-white/10 p-5 xl:flex xl:min-h-0 xl:flex-col xl:overflow-hidden xl:border-b-0 xl:border-r">
        <div className="xl:min-h-0 xl:flex-1 xl:overflow-y-auto xl:pr-1">
          <div>
            <div className="apple-kicker">检索模式</div>
            <h2 className="mt-2 text-2xl font-semibold tracking-[-0.03em] text-white">知识树</h2>
            <p className="mt-3 text-sm leading-6 text-slate-400">支持目录上传保留相对路径，也支持手动建节点后继续补文件。</p>
          </div>

          <div className="apple-panel mt-5 rounded-[28px] p-4">
            <div className="flex items-center justify-between gap-3">
              <div className="text-sm font-medium text-white">当前节点</div>
              {selectedNodeId !== "root" && (
                <button
                  className="apple-status-danger rounded-full px-2.5 py-1 text-xs disabled:opacity-50"
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

          <div className="apple-panel mt-5 rounded-[28px] p-4">
            <div className="text-sm font-medium text-white">新建节点</div>
            <input
              className="apple-input mt-3 w-full rounded-[18px] px-3 py-2.5 text-sm"
              value={newNodeName}
              onChange={(event) => setNewNodeName(event.target.value)}
              placeholder="例如：项目周报"
            />
            <button
              className="apple-button-secondary mt-3 w-full rounded-[18px] px-3 py-2.5 text-sm"
              onClick={() => {
                void handleCreateNode();
              }}
              disabled={isBusy}
            >
              在当前节点下创建
            </button>
          </div>

          <div className="apple-panel mt-5 rounded-[28px] p-4">
            <div className="text-sm font-medium text-white">上传</div>
            <div className="mt-3 grid gap-3">
              <label className="apple-button-secondary rounded-[18px] px-3 py-2.5 text-sm text-slate-300">
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
              <label className="apple-button-secondary rounded-[18px] px-3 py-2.5 text-sm text-slate-300">
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

            <div className="mt-4 border-t border-white/10 pt-4">
              <div className="text-sm font-medium text-white">导入 GitLab 文档树</div>
              <input
                className="apple-input mt-3 w-full rounded-[18px] px-3 py-2.5 text-sm"
                value={gitlabTreeUrl}
                onChange={(event) => setGitlabTreeUrl(event.target.value)}
                placeholder="粘贴 GitLab `/-/tree/<ref>/<path>` 地址"
              />
              <button
                className="apple-button-secondary mt-3 w-full rounded-[18px] px-3 py-2.5 text-sm"
                onClick={() => {
                  void handleGitLabImport();
                }}
                disabled={isBusy || gitlabTreeUrl.trim() === ""}
              >
                批量导入到当前节点
              </button>
              <div className="mt-3 text-xs leading-6 text-slate-500">
                第一版仅支持 `git.yyrd.com` 的 GitLab tree 链接；凭据可在左下角“工作台设置 → GitLab 导入设置”里配置。
              </div>
              <div className="mt-1 text-xs leading-6 text-slate-500">
                导入后，支持问题 Agent 只要把知识范围指向当前节点的递归范围，就能检索到这些文档。
              </div>
            </div>

            {gitlabImportResult != null && (
              <div className="apple-panel-subtle mt-4 rounded-[24px] p-4">
                <div className="text-sm font-medium text-white">最近一次 GitLab 导入</div>
                <div className="mt-2 break-all text-xs leading-6 text-slate-400">{gitlabImportResult.source_url}</div>
                <div className="mt-3 grid gap-2 text-xs text-slate-400">
                  <div>新增：{gitlabImportResult.created_count}</div>
                  <div>覆盖更新：{gitlabImportResult.updated_count}</div>
                  <div>跳过：{gitlabImportResult.skipped_count}</div>
                  <div>失败：{gitlabImportResult.failed_count}</div>
                </div>

                {gitlabImportResult.failed_items.length > 0 && (
                  <div className="mt-3 space-y-2">
                    <div className="text-xs font-medium text-rose-200">失败原因</div>
                    {gitlabImportResult.failed_items.slice(0, 5).map((item) => (
                      <div key={item.path + item.reason} className="text-xs leading-5 text-rose-100">
                        {item.path}：{item.reason}
                      </div>
                    ))}
                    {gitlabImportResult.failed_items.length > 5 && (
                      <div className="text-xs text-slate-500">还有 {gitlabImportResult.failed_items.length - 5} 条失败信息未展开</div>
                    )}
                  </div>
                )}

                {gitlabImportResult.skipped_paths.length > 0 && (
                  <div className="mt-3 space-y-2">
                    <div className="text-xs font-medium text-amber-200">已跳过的不支持文件</div>
                    {gitlabImportResult.skipped_paths.slice(0, 5).map((path) => (
                      <div key={path} className="text-xs leading-5 text-amber-100">
                        {path}
                      </div>
                    ))}
                    {gitlabImportResult.skipped_paths.length > 5 && (
                      <div className="text-xs text-slate-500">还有 {gitlabImportResult.skipped_paths.length - 5} 个文件未展开</div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="apple-panel-subtle mt-5 rounded-[28px] p-3">
            {tree != null && <KnowledgeTree node={tree.root} selectedId={selectedNodeId} onSelect={setSelectedNodeId} />}
          </div>
        </div>
      </aside>

      <main className="min-w-0 px-6 py-6 xl:min-h-0 xl:overflow-y-auto">
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
          <section className="apple-panel rounded-[32px] p-5">
            <div className="flex items-center justify-between">
              <div>
                <div className="apple-kicker">Scoped Retrieval</div>
                <h3 className="mt-2 text-2xl font-semibold tracking-[-0.03em] text-white">检索表单</h3>
              </div>
              <div className="text-xs text-slate-500">当前节点：{detail?.node.name ?? "全部知识"}</div>
            </div>

            <div className="mt-5 grid gap-4 lg:grid-cols-2">
              <label className="grid gap-1 text-sm">
                <span className="text-slate-400">Scope</span>
                <select
                  className="apple-select rounded-[18px] px-3 py-2.5"
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
              className="apple-textarea mt-5 min-h-32 w-full rounded-[24px] px-4 py-3.5 text-sm leading-7 outline-none"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />

            <div className="mt-4 flex items-center justify-between">
              <div className="text-xs text-slate-500">
                {scopeType === "global" ? "会跨全部文档检索" : "只会检索当前节点及其全部子节点"}
              </div>
              <button
                className="apple-button-primary rounded-full px-5 py-2.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50"
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
                <div className="apple-status-success rounded-[24px] p-4">
                  <div className="text-sm font-medium text-emerald-100">LLM 汇总</div>
                  <div className="mt-3 whitespace-pre-wrap text-sm leading-7 text-slate-50">{result.summary}</div>
                </div>

                {result.related_document_links.length > 0 && (
                  <div className="apple-panel-subtle rounded-[24px] p-4">
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

                <div className="apple-panel-subtle rounded-[24px] p-4">
                  <div className="text-sm font-medium text-white">检索上下文</div>
                  <pre className="mt-3 whitespace-pre-wrap text-xs leading-6 text-slate-400">{result.retrieval_context}</pre>
                </div>

                {result.debug != null && (
                  <div className="apple-panel-subtle rounded-[24px] p-4">
                    <div className="text-sm font-medium text-white">检索调试</div>
                    <div className="mt-3 grid gap-3 text-xs text-slate-300 lg:grid-cols-3">
                      <div>Profile：{result.debug.retrieval_profile}</div>
                      <div>候选数：{result.debug.candidate_count}</div>
                      <div>入选证据：{result.debug.selected_count}</div>
                    </div>

                    <div className="mt-4">
                      <div className="text-xs font-medium text-sky-200">Query Bundle</div>
                      <div className="mt-2 space-y-2">
                        {result.debug.query_bundle.query_variants.map((item) => (
                          <div key={item.label + item.query} className="rounded-2xl border border-white/10 px-3 py-2">
                            <div className="text-[11px] uppercase tracking-[0.12em] text-slate-500">{item.label}</div>
                            <div className="mt-1 whitespace-pre-wrap text-sm text-slate-100">{item.query}</div>
                          </div>
                        ))}
                      </div>
                    </div>

                    {result.debug.rerank_preview.length > 0 && (
                      <div className="mt-4">
                        <div className="text-xs font-medium text-amber-200">Rerank Preview</div>
                        <div className="mt-2 space-y-3">
                          {result.debug.rerank_preview.slice(0, 6).map((item) => (
                            <div key={item.chunk_id} className="rounded-2xl border border-white/10 px-3 py-3">
                              <div className="flex items-center justify-between gap-3">
                                <div className="text-sm font-medium text-slate-100">{item.document_name}</div>
                                <div className="text-[11px] text-slate-500">
                                  score {item.relevance_score.toFixed(2)} / fused {item.fused_score.toFixed(2)}
                                </div>
                              </div>
                              <div className="mt-1 text-[11px] text-slate-500">{item.heading_path || item.tree_path || "/"}</div>
                              <div className="mt-2 whitespace-pre-wrap text-xs leading-6 text-slate-400">{item.snippet}</div>
                              <div className="mt-2 text-xs text-emerald-200">{item.reason || "无 rerank 原因"}</div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}

                <div className="space-y-3">
                  <div className="text-sm font-medium text-white">引用片段</div>
                  {result.citations.map((citation) => (
                    <div key={citation.chunk_id} className="apple-panel-subtle rounded-[24px] p-4">
                      <div className="flex items-center justify-between gap-3">
                        <div className="font-medium text-amber-200">{citation.document_name}</div>
                        <div className="text-xs text-slate-500">{citation.heading_path || citation.tree_path || "/"}</div>
                      </div>
                      <div className="mt-2 text-xs text-slate-500">{citation.relative_path || citation.document_name}</div>
                      <div className="mt-3 whitespace-pre-wrap text-sm text-slate-300">{citation.snippet}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>

          <section className="apple-panel rounded-[32px] p-5">
            <h3 className="text-xl font-semibold tracking-[-0.03em] text-white">当前节点详情</h3>
            <div className="mt-4 space-y-3 text-sm text-slate-400">
              <div>名称：{detail?.node.name ?? "-"}</div>
              <div>路径：{detail?.node.path ?? "/"}</div>
              <div>直属子节点：{detail?.children.length ?? 0}</div>
              <div>直属文件：{detail?.documents.length ?? 0}</div>
            </div>

            <div className="mt-5">
              <div className="text-sm font-medium text-white">直属文件</div>
              <div className="mt-3 space-y-3">
                {(detail?.documents ?? []).map((document) => (
                  <div key={document.id} className="apple-panel-subtle rounded-[24px] p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="font-medium text-slate-200">{document.name}</div>
                      <button
                        className="apple-status-danger rounded-full px-2.5 py-1 text-xs disabled:opacity-50"
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
                        className="apple-input rounded-[18px] px-3 py-2.5 text-xs text-slate-100 outline-none"
                        value={linkDrafts[document.id] ?? ""}
                        onChange={(event) =>
                          setLinkDrafts((current) => ({ ...current, [document.id]: event.target.value }))
                        }
                        placeholder="给这个文档配置线上链接，支持问题 Agent 命中后会回写到表格"
                      />
                      <button
                        className="apple-button-secondary rounded-[18px] px-3 py-2.5 text-xs disabled:opacity-50"
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

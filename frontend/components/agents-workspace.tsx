"use client";

import { useEffect, useMemo, useState } from "react";

import { createAgent, getAgent, getCatalog, getKnowledgeTree, listAgents, runAgent, updateAgent } from "../lib/api";
import type { AgentConfig, AgentRunResponse, Catalog, KnowledgeTreeNode, KnowledgeTreeResponse, ModelConfig, ScopeType } from "../lib/types";
import { ModelSelector } from "./model-selector";
import { useModelSettings } from "./model-settings-provider";

const DEFAULT_MODEL: ModelConfig = {
  mode: "learning",
  provider: "mock",
  model: "learning-mode",
  temperature: 0.2,
  max_tokens: 1024
};

type AgentFormState = {
  id?: string;
  name: string;
  description: string;
  system_prompt: string;
  model_config: ModelConfig;
  enabled_skills: string[];
  knowledge_scope_type: ScopeType;
  knowledge_scope_id?: string | null;
};

function flattenTree(node: KnowledgeTreeNode): Array<{ id: string; label: string }> {
  const current = [{ id: node.id, label: node.path + " · " + node.name }];
  return current.concat(node.children.flatMap(flattenTree));
}

function buildEmptyForm(defaultSkills: string[]): AgentFormState {
  return {
    name: "新的 Agent",
    description: "",
    system_prompt: "你是一个带知识范围和 Skill 的学习型 Agent。",
    model_config: DEFAULT_MODEL,
    enabled_skills: defaultSkills,
    knowledge_scope_type: "none",
    knowledge_scope_id: null
  };
}

export function AgentsWorkspace() {
  const { validateModelConfig, openModelSettings } = useModelSettings();
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [tree, setTree] = useState<KnowledgeTreeResponse | null>(null);
  const [agents, setAgents] = useState<AgentConfig[]>([]);
  const [form, setForm] = useState<AgentFormState>(buildEmptyForm([]));
  const [runInput, setRunInput] = useState("请基于你的配置回答这个问题");
  const [runResult, setRunResult] = useState<AgentRunResponse | null>(null);
  const [error, setError] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const modelValidation = validateModelConfig(form.model_config);

  const treeOptions = useMemo(() => {
    if (tree == null) return [];
    return flattenTree(tree.root);
  }, [tree]);

  async function bootstrap() {
    // Agent 页面启动时要同时拿三类信息：
    // 1. catalog：有哪些 skills 可选；
    // 2. knowledge tree：有哪些知识范围可绑定；
    // 3. agents：已有的配置型 Agent。
    const [catalogData, treeData, agentsData] = await Promise.all([getCatalog(), getKnowledgeTree(), listAgents()]);
    setCatalog(catalogData);
    setTree(treeData);
    setAgents(agentsData);

    const defaultSkillIds = catalogData.skills.filter((skill) => skill.enabled_by_default).map((skill) => skill.id);
    if (agentsData.length > 0) {
      const detail = await getAgent(agentsData[0].id);
      setForm(detail);
    } else {
      setForm(buildEmptyForm(defaultSkillIds));
    }
  }

  useEffect(() => {
    bootstrap().catch((cause) => setError(String(cause)));
  }, []);

  async function refreshAgents(selectId?: string) {
    const nextAgents = await listAgents();
    setAgents(nextAgents);
    if (selectId != null && selectId !== "") {
      const detail = await getAgent(selectId);
      setForm(detail);
      return;
    }
    if (nextAgents.length > 0) {
      const detail = await getAgent(nextAgents[0].id);
      setForm(detail);
    }
  }

  async function handleSave() {
    // 保存阶段只是把配置落库，还没有真正执行 Agent。
    // 这样你可以先配置，再在右侧运行面板里反复试不同问题。
    if (form.name.trim() === "") return;
    if (!modelValidation.isRunnable) {
      setError(modelValidation.message);
      openModelSettings(form.model_config.provider);
      return;
    }
    setError("");
    setIsSaving(true);
    try {
      if (form.id == null) {
        const created = await createAgent({
          name: form.name,
          description: form.description,
          system_prompt: form.system_prompt,
          model_config: form.model_config,
          enabled_skills: form.enabled_skills,
          knowledge_scope_type: form.knowledge_scope_type,
          knowledge_scope_id: form.knowledge_scope_type === "tree_recursive" ? form.knowledge_scope_id : null
        });
        await refreshAgents(created.id);
      } else {
        const updated = await updateAgent(form.id, {
          name: form.name,
          description: form.description,
          system_prompt: form.system_prompt,
          model_config: form.model_config,
          enabled_skills: form.enabled_skills,
          knowledge_scope_type: form.knowledge_scope_type,
          knowledge_scope_id: form.knowledge_scope_type === "tree_recursive" ? form.knowledge_scope_id : null
        });
        await refreshAgents(updated.id);
      }
    } catch (cause) {
      setError(String(cause));
    } finally {
      setIsSaving(false);
    }
  }

  async function handleRun() {
    // 运行时会把当前 Agent 的 prompt / skills / knowledge scope 一起带进后端。
    if (!modelValidation.isRunnable) {
      setError(modelValidation.message);
      openModelSettings(form.model_config.provider);
      return;
    }
    if (form.id == null) {
      await handleSave();
    }
    if (form.id == null || runInput.trim() === "") return;
    setError("");
    setIsRunning(true);
    try {
      const nextResult = await runAgent(form.id, runInput);
      setRunResult(nextResult);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setIsRunning(false);
    }
  }

  return (
    <div className="grid min-h-screen grid-cols-[300px_minmax(0,1fr)_420px]">
      <aside className="border-r border-slate-800 bg-slate-900/50 p-5">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-xs uppercase tracking-[0.35em] text-amber-300">我的 Agent</div>
            <h2 className="mt-2 text-xl font-semibold">Agent 列表</h2>
          </div>
          <button
            className="rounded-xl border border-slate-700 px-3 py-2 text-sm hover:border-amber-300"
            onClick={() => {
              const defaultSkills = catalog?.skills.filter((skill) => skill.enabled_by_default).map((skill) => skill.id) ?? [];
              setForm(buildEmptyForm(defaultSkills));
              setRunResult(null);
            }}
          >
            新建
          </button>
        </div>

        <div className="mt-5 space-y-3">
          {agents.map((agent) => (
            <button
              key={agent.id}
              className={
                "w-full rounded-2xl border p-4 text-left transition " +
                (form.id === agent.id
                  ? "border-amber-300/60 bg-amber-300/10"
                  : "border-slate-800 bg-slate-900 hover:border-slate-700")
              }
              onClick={async () => {
                const detail = await getAgent(agent.id);
                setForm(detail);
                setRunResult(null);
              }}
            >
              <div className="font-medium">{agent.name}</div>
              <div className="mt-2 line-clamp-2 text-sm text-slate-400">{agent.description || agent.system_prompt}</div>
              <div className="mt-3 text-xs text-slate-500">scope: {agent.knowledge_scope_type}</div>
            </button>
          ))}
          {agents.length === 0 && <div className="text-sm text-slate-500">还没有配置型 Agent，可以先创建一个。</div>}
        </div>
      </aside>

      <main className="min-w-0 border-r border-slate-800 px-6 py-6">
        <div className="rounded-3xl border border-slate-800 bg-slate-900 p-5">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm text-slate-400">配置型 Agent v1</div>
              <h3 className="mt-1 text-xl font-semibold">Agent 配置</h3>
            </div>
            <button
              className="rounded-xl bg-amber-300 px-4 py-2 text-sm font-medium text-slate-950 hover:bg-amber-200 disabled:bg-slate-700"
              onClick={() => {
                void handleSave();
              }}
              disabled={isSaving}
            >
              {isSaving ? "保存中..." : "保存配置"}
            </button>
          </div>

          <div className="mt-5 grid gap-4 lg:grid-cols-2">
            <label className="grid gap-1 text-sm lg:col-span-2">
              <span className="text-slate-400">名称</span>
              <input
                className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                value={form.name}
                onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
              />
            </label>
            <label className="grid gap-1 text-sm lg:col-span-2">
              <span className="text-slate-400">描述</span>
              <input
                className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                value={form.description}
                onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))}
              />
            </label>
            <label className="grid gap-1 text-sm lg:col-span-2">
              <span className="text-slate-400">System Prompt</span>
              <textarea
                className="min-h-36 rounded-2xl border border-slate-700 bg-slate-950 px-3 py-3 leading-6"
                value={form.system_prompt}
                onChange={(event) => setForm((current) => ({ ...current, system_prompt: event.target.value }))}
              />
            </label>
            <div className="lg:col-span-2">
              <ModelSelector
                value={form.model_config}
                onChange={(nextModelConfig) => setForm((current) => ({ ...current, model_config: nextModelConfig }))}
              />
            </div>
            <label className="grid gap-1 text-sm">
              <span className="text-slate-400">知识范围</span>
              <select
                className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                value={form.knowledge_scope_type}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    knowledge_scope_type: event.target.value as ScopeType,
                    knowledge_scope_id: event.target.value === "tree_recursive" ? current.knowledge_scope_id ?? "root" : null
                  }))
                }
              >
                <option value="none">none</option>
                <option value="global">global</option>
                <option value="tree_recursive">tree_recursive</option>
              </select>
            </label>
            {form.knowledge_scope_type === "tree_recursive" && (
              <label className="grid gap-1 text-sm">
                <span className="text-slate-400">树节点</span>
                <select
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
                  value={form.knowledge_scope_id ?? "root"}
                  onChange={(event) => setForm((current) => ({ ...current, knowledge_scope_id: event.target.value }))}
                >
                  {treeOptions.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>

          <div className="mt-6">
            <div className="text-sm font-medium text-slate-200">启用 Skills</div>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              {catalog?.skills.map((skill) => (
                <label key={skill.id} className="flex items-start gap-3 rounded-2xl border border-slate-800 p-3">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={form.enabled_skills.includes(skill.id)}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        enabled_skills: event.target.checked
                          ? current.enabled_skills.concat(skill.id)
                          : current.enabled_skills.filter((item) => item !== skill.id)
                      }))
                    }
                  />
                  <div>
                    <div className="font-medium">{skill.name}</div>
                    <div className="mt-1 text-sm text-slate-400">{skill.description}</div>
                  </div>
                </label>
              ))}
            </div>
          </div>
        </div>
        {error !== "" && <div className="mt-6 text-sm text-rose-300">{error}</div>}
      </main>

      <aside className="bg-slate-900/60 p-5">
        <div className="rounded-3xl border border-slate-800 bg-slate-900 p-5">
          <div className="text-sm text-slate-400">专属运行面板</div>
          <h3 className="mt-1 text-xl font-semibold">运行 Agent</h3>

          <textarea
            className="mt-5 min-h-40 w-full rounded-2xl border border-slate-800 bg-slate-950/60 px-4 py-3 text-sm leading-6 outline-none"
            value={runInput}
            onChange={(event) => setRunInput(event.target.value)}
          />

          <button
            className="mt-4 w-full rounded-xl bg-sky-500 px-4 py-2 text-sm font-medium text-slate-950 hover:bg-sky-400 disabled:bg-slate-700"
            onClick={() => {
              void handleRun();
            }}
            disabled={isRunning || !modelValidation.isRunnable}
          >
            {isRunning ? "运行中..." : "运行 Agent"}
          </button>

          {runResult != null && (
            <div className="mt-6 space-y-4">
              <div className="rounded-2xl border border-emerald-400/30 bg-emerald-400/10 p-4">
                <div className="text-sm font-medium text-emerald-200">结果</div>
                <div className="mt-3 whitespace-pre-wrap text-sm leading-6">{runResult.result.answer}</div>
              </div>

              {runResult.citations.length > 0 && (
                <div className="space-y-3">
                  <div className="text-sm font-medium text-slate-200">引用</div>
                  {runResult.citations.map((citation) => (
                    <div key={citation.chunk_id} className="rounded-2xl border border-slate-800 bg-slate-950/50 p-4">
                      <div className="font-medium text-sky-200">{citation.document_name}</div>
                      <div className="mt-2 text-xs text-slate-500">{citation.tree_path || "/"}</div>
                      <div className="mt-3 whitespace-pre-wrap text-sm text-slate-300">{citation.snippet}</div>
                    </div>
                  ))}
                </div>
              )}

              <div className="rounded-2xl border border-slate-800 bg-slate-950/50 p-4">
                <div className="text-sm font-medium text-slate-200">检索上下文</div>
                <pre className="mt-3 whitespace-pre-wrap text-xs leading-6 text-slate-400">{runResult.retrieval_context}</pre>
              </div>
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}

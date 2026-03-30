"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import {
  listSupportAgents,
  listSupportAgentCaseCandidates,
  reviewSupportCaseCandidate,
  syncSupportAgentFeedback
} from "../lib/api";
import type {
  SupportIssueAgentConfig,
  SupportIssueCaseCandidate,
  SupportIssueFeedbackSyncResponse
} from "../lib/types";

/**
 * 案例页只保留两态：
 * - 待审核：人工内容可以继续改，尚未进入正式案例库；
 * - 审核通过：已经进入正式案例库，可被后续检索复用。
 *
 * 这里把状态文案收敛成一个函数，避免页面里散落硬编码。
 */
function mapCaseStatusLabel(status: SupportIssueCaseCandidate["status"]) {
  return status === "approved" ? "审核通过" : "待审核";
}

/**
 * 候选“名称”并不单独存库，而是始终取问题首行。
 * 这样可以减少一份冗余字段，同时满足列表页搜索与展示需求。
 */
function buildCandidateName(question: string, recordId: string) {
  const firstLine = question.trim().split("\n")[0]?.trim() || "";
  return firstLine !== "" ? firstLine : `未命名案例-${recordId}`;
}

function formatDate(value?: string | null) {
  if (value == null || value === "") return "-";
  return new Date(value).toLocaleString("zh-CN");
}

function formatPercent(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}

export function SupportIssueCaseCandidatesWorkspace() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryAgentId = (searchParams.get("agentId") || "").trim();

  const [agents, setAgents] = useState<SupportIssueAgentConfig[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState("");
  const [caseCandidates, setCaseCandidates] = useState<SupportIssueCaseCandidate[]>([]);
  const [selectedCandidateId, setSelectedCandidateId] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "pending_review" | "approved">("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [keyword, setKeyword] = useState("");
  const [reviewerName, setReviewerName] = useState("平台管理员");
  const [draftFinalSolution, setDraftFinalSolution] = useState("");
  const [draftFeedbackComment, setDraftFeedbackComment] = useState("");
  const [feedbackSyncResult, setFeedbackSyncResult] = useState<SupportIssueFeedbackSyncResponse | null>(null);
  const [error, setError] = useState("");
  const [isBootstrapping, setIsBootstrapping] = useState(true);
  const [isLoadingCandidates, setIsLoadingCandidates] = useState(false);
  const [isSyncingFeedback, setIsSyncingFeedback] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  /**
   * 独立案例页仍然依附某个 Support Agent。
   * 因此首次进入页面时，需要先确定当前 agent：
   * - 优先使用 URL 里的 `agentId`；
   * - URL 没传时，默认取第一个 Agent。
   */
  useEffect(() => {
    let cancelled = false;

    async function bootstrap() {
      setIsBootstrapping(true);
      setError("");
      try {
        const nextAgents = await listSupportAgents();
        if (cancelled) return;
        setAgents(nextAgents);
        const fallbackAgentId = nextAgents[0]?.id ?? "";
        const nextSelectedAgentId =
          nextAgents.find((item) => item.id === queryAgentId)?.id ?? fallbackAgentId;
        setSelectedAgentId(nextSelectedAgentId);
      } catch (cause) {
        if (!cancelled) {
          setError(String(cause));
        }
      } finally {
        if (!cancelled) {
          setIsBootstrapping(false);
        }
      }
    }

    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, [queryAgentId]);

  /**
   * 切换 Agent 后重新加载候选数据，并同步 URL，方便从主页面跳转后直接落到对应 Agent。
   */
  useEffect(() => {
    let cancelled = false;

    async function loadCandidates() {
      if (selectedAgentId === "") {
        setCaseCandidates([]);
        setSelectedCandidateId("");
        return;
      }

      setIsLoadingCandidates(true);
      setError("");
      try {
        const nextCandidates = await listSupportAgentCaseCandidates(selectedAgentId);
        if (cancelled) return;
        setCaseCandidates(nextCandidates);
        setSelectedCandidateId((current) => {
          if (current !== "" && nextCandidates.some((item) => item.id === current)) {
            return current;
          }
          return nextCandidates[0]?.id ?? "";
        });

        const nextUrl = `/support-agents/cases?agentId=${encodeURIComponent(selectedAgentId)}`;
        if (searchParams.toString() !== `agentId=${encodeURIComponent(selectedAgentId)}`) {
          router.replace(nextUrl);
        }
      } catch (cause) {
        if (!cancelled) {
          setError(String(cause));
        }
      } finally {
        if (!cancelled) {
          setIsLoadingCandidates(false);
        }
      }
    }

    void loadCandidates();
    return () => {
      cancelled = true;
    };
  }, [router, searchParams, selectedAgentId]);

  const filteredCandidates = useMemo(() => {
    return caseCandidates.filter((candidate) => {
      if (statusFilter !== "all" && candidate.status !== statusFilter) {
        return false;
      }
      if (categoryFilter !== "all" && candidate.question_category !== categoryFilter) {
        return false;
      }
      const normalizedKeyword = keyword.trim().toLowerCase();
      if (normalizedKeyword === "") {
        return true;
      }
      const searchableText = [
        buildCandidateName(candidate.question, candidate.record_id),
        candidate.question,
        candidate.record_id,
        candidate.final_solution
      ]
        .join("\n")
        .toLowerCase();
      return searchableText.includes(normalizedKeyword);
    });
  }, [caseCandidates, categoryFilter, keyword, statusFilter]);

  const categoryOptions = useMemo(() => {
    return Array.from(
      new Set(
        caseCandidates
          .map((item) => item.question_category.trim())
          .filter((item) => item !== "")
      )
    ).sort((left, right) => left.localeCompare(right, "zh-CN"));
  }, [caseCandidates]);

  const selectedCandidate = useMemo(() => {
    return caseCandidates.find((item) => item.id === selectedCandidateId) ?? null;
  }, [caseCandidates, selectedCandidateId]);

  /**
   * 右侧详情区始终以当前选中的候选为准。
   * 每次切换行时，把草稿重置为数据库里的最新值，避免上一条的编辑内容串到下一条。
   */
  useEffect(() => {
    setDraftFinalSolution(selectedCandidate?.final_solution ?? "");
    setDraftFeedbackComment(selectedCandidate?.feedback_comment ?? "");
  }, [selectedCandidate]);

  /**
   * 过滤器变化后，如果当前选中行不在结果集里，就自动切到筛选后的第一条。
   */
  useEffect(() => {
    if (filteredCandidates.some((item) => item.id === selectedCandidateId)) {
      return;
    }
    setSelectedCandidateId(filteredCandidates[0]?.id ?? "");
  }, [filteredCandidates, selectedCandidateId]);

  async function refreshCandidates(options?: { keepSelectedCandidateId?: string }) {
    if (selectedAgentId === "") return;
    const nextCandidates = await listSupportAgentCaseCandidates(selectedAgentId);
    setCaseCandidates(nextCandidates);
    const preferredId = options?.keepSelectedCandidateId ?? selectedCandidateId;
    setSelectedCandidateId(
      nextCandidates.some((item) => item.id === preferredId) ? preferredId : nextCandidates[0]?.id ?? ""
    );
  }

  async function handleSyncFeedback() {
    if (selectedAgentId === "") return;

    setIsSyncingFeedback(true);
    setError("");
    try {
      const result = await syncSupportAgentFeedback(selectedAgentId);
      setFeedbackSyncResult(result);
      await refreshCandidates();
    } catch (cause) {
      setError(String(cause));
    } finally {
      setIsSyncingFeedback(false);
    }
  }

  async function handleSubmit(action: "save_edit" | "approve_and_publish") {
    if (selectedCandidate == null) return;

    setIsSubmitting(true);
    setError("");
    try {
      const updated = await reviewSupportCaseCandidate(selectedCandidate.id, {
        action,
        reviewer_name: reviewerName,
        final_solution: draftFinalSolution,
        feedback_comment: draftFeedbackComment,
        sync_to_feishu: true
      });
      await refreshCandidates({ keepSelectedCandidateId: updated.id });
    } catch (cause) {
      setError(String(cause));
    } finally {
      setIsSubmitting(false);
    }
  }

  const pendingCount = caseCandidates.filter((item) => item.status === "pending_review").length;
  const approvedCount = caseCandidates.filter((item) => item.status === "approved").length;

  return (
    <div className="min-h-screen bg-slate-950 px-6 py-6 text-slate-100">
      <div className="rounded-3xl border border-slate-800 bg-slate-900 p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="text-sm text-slate-400">支持问题 Agent / 独立案例审核台</div>
            <h2 className="mt-1 text-2xl font-semibold">案例候选池</h2>
            <div className="mt-2 text-sm leading-6 text-slate-500">
              这里集中查看当前 Agent 的全部候选案例。页面只保留两态：待审核、审核通过。
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            <Link
              href="/support-agents"
              className="rounded-xl border border-slate-700 px-4 py-2 text-sm text-slate-200 transition hover:border-slate-500"
            >
              返回 Agent 配置
            </Link>
            <button
              className="rounded-xl border border-cyan-400/40 px-4 py-2 text-sm text-cyan-200 transition hover:bg-cyan-400/10 disabled:border-slate-800 disabled:text-slate-500"
              onClick={() => {
                void handleSyncFeedback();
              }}
              disabled={selectedAgentId === "" || isSyncingFeedback || isSubmitting}
            >
              {isSyncingFeedback ? "同步中..." : "同步反馈"}
            </button>
          </div>
        </div>

        {feedbackSyncResult != null && (
          <div className="mt-4 rounded-2xl border border-cyan-400/20 bg-cyan-400/10 px-4 py-3 text-sm text-cyan-100">
            {feedbackSyncResult.summary}
          </div>
        )}

        {error !== "" && (
          <div className="mt-4 rounded-2xl border border-rose-400/20 bg-rose-400/10 px-4 py-3 text-sm text-rose-200">
            {error}
          </div>
        )}

        <div className="mt-5 grid gap-3 lg:grid-cols-[220px_200px_200px_minmax(0,1fr)_220px]">
          <label className="grid gap-1 text-sm">
            <span className="text-slate-400">当前 Agent</span>
            <select
              className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
              value={selectedAgentId}
              onChange={(event) => setSelectedAgentId(event.target.value)}
              disabled={isBootstrapping || agents.length === 0}
            >
              {agents.map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agent.name}
                </option>
              ))}
            </select>
          </label>

          <label className="grid gap-1 text-sm">
            <span className="text-slate-400">状态筛选</span>
            <select
              className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value as "all" | "pending_review" | "approved")}
            >
              <option value="all">全部</option>
              <option value="pending_review">待审核</option>
              <option value="approved">审核通过</option>
            </select>
          </label>

          <label className="grid gap-1 text-sm">
            <span className="text-slate-400">分类筛选</span>
            <select
              className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
              value={categoryFilter}
              onChange={(event) => setCategoryFilter(event.target.value)}
            >
              <option value="all">全部分类</option>
              {categoryOptions.map((category) => (
                <option key={category} value={category}>
                  {category}
                </option>
              ))}
            </select>
          </label>

          <label className="grid gap-1 text-sm">
            <span className="text-slate-400">名称 / 关键词搜索</span>
            <input
              className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              placeholder="搜索名称、问题、记录ID、人工最终方案"
            />
          </label>

          <label className="grid gap-1 text-sm">
            <span className="text-slate-400">审核人</span>
            <input
              className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2"
              value={reviewerName}
              onChange={(event) => setReviewerName(event.target.value)}
              placeholder="例如：平台管理员"
            />
          </label>
        </div>

        <div className="mt-5 grid gap-3 md:grid-cols-3">
          <div className="rounded-2xl border border-slate-800 bg-slate-950/50 px-4 py-4">
            <div className="text-xs text-slate-500">候选总数</div>
            <div className="mt-2 text-2xl font-semibold">{caseCandidates.length}</div>
          </div>
          <div className="rounded-2xl border border-slate-800 bg-slate-950/50 px-4 py-4">
            <div className="text-xs text-slate-500">待审核</div>
            <div className="mt-2 text-2xl font-semibold text-amber-200">{pendingCount}</div>
          </div>
          <div className="rounded-2xl border border-slate-800 bg-slate-950/50 px-4 py-4">
            <div className="text-xs text-slate-500">审核通过</div>
            <div className="mt-2 text-2xl font-semibold text-emerald-200">{approvedCount}</div>
          </div>
        </div>
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,1fr)_420px]">
        <section className="overflow-hidden rounded-3xl border border-slate-800 bg-slate-900">
          <div className="border-b border-slate-800 px-5 py-4">
            <div className="text-sm text-slate-400">表格视图</div>
            <div className="mt-1 text-lg font-semibold">候选案例列表</div>
          </div>

          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-800 text-sm">
              <thead className="bg-slate-950/60 text-left text-slate-400">
                <tr>
                  <th className="px-4 py-3 font-medium">名称</th>
                  <th className="px-4 py-3 font-medium">状态</th>
                  <th className="px-4 py-3 font-medium">问题分类</th>
                  <th className="px-4 py-3 font-medium">人工处理结果</th>
                  <th className="px-4 py-3 font-medium">AI置信度</th>
                  <th className="px-4 py-3 font-medium">命中知识数</th>
                  <th className="px-4 py-3 font-medium">更新时间</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800">
                {filteredCandidates.map((candidate) => {
                  const isSelected = candidate.id === selectedCandidateId;
                  return (
                    <tr
                      key={candidate.id}
                      className={
                        "cursor-pointer transition " +
                        (isSelected ? "bg-amber-300/10" : "hover:bg-slate-950/40")
                      }
                      onClick={() => setSelectedCandidateId(candidate.id)}
                    >
                      <td className="px-4 py-3 align-top">
                        <div className="font-medium text-slate-100">
                          {buildCandidateName(candidate.question, candidate.record_id)}
                        </div>
                        <div className="mt-1 text-xs text-slate-500">{candidate.record_id}</div>
                      </td>
                      <td className="px-4 py-3 align-top text-xs text-slate-300">
                        {mapCaseStatusLabel(candidate.status)}
                      </td>
                      <td className="px-4 py-3 align-top text-xs text-slate-300">
                        {candidate.question_category || "-"}
                      </td>
                      <td className="px-4 py-3 align-top text-xs text-slate-300">
                        {candidate.feedback_result || "-"}
                      </td>
                      <td className="px-4 py-3 align-top text-xs text-slate-300">
                        {formatPercent(candidate.confidence_score)}
                      </td>
                      <td className="px-4 py-3 align-top text-xs text-slate-300">
                        {candidate.retrieval_hit_count}
                      </td>
                      <td className="px-4 py-3 align-top text-xs text-slate-300">
                        {formatDate(candidate.updated_at)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {!isLoadingCandidates && filteredCandidates.length === 0 && (
            <div className="px-5 py-10 text-sm text-slate-500">
              当前筛选条件下没有案例。可以先同步反馈，或者调整状态 / 分类 / 关键词筛选。
            </div>
          )}

          {isLoadingCandidates && (
            <div className="px-5 py-10 text-sm text-slate-500">正在读取案例候选数据...</div>
          )}
        </section>

        <aside className="rounded-3xl border border-slate-800 bg-slate-900 p-5">
          <div>
            <div className="text-sm text-slate-400">详情与编辑</div>
            <div className="mt-1 text-lg font-semibold">当前候选</div>
          </div>

          {selectedCandidate == null ? (
            <div className="mt-6 rounded-2xl border border-dashed border-slate-700 px-4 py-8 text-sm text-slate-500">
              请选择左侧表格中的一条候选案例。
            </div>
          ) : (
            <div className="mt-5 space-y-4">
              <div className="rounded-2xl border border-slate-800 bg-slate-950/50 p-4">
                <div className="text-xs text-slate-500">名称</div>
                <div className="mt-2 font-medium text-slate-100">
                  {buildCandidateName(selectedCandidate.question, selectedCandidate.record_id)}
                </div>
                <div className="mt-3 grid gap-1 text-xs text-slate-400">
                  <div>状态：{mapCaseStatusLabel(selectedCandidate.status)}</div>
                  <div>来源飞书记录 ID：{selectedCandidate.record_id}</div>
                  <div>问题分类：{selectedCandidate.question_category || "-"}</div>
                  <div>人工处理结果：{selectedCandidate.feedback_result || "-"}</div>
                  <div>AI 置信度：{formatPercent(selectedCandidate.confidence_score)}</div>
                  <div>命中知识数：{selectedCandidate.retrieval_hit_count}</div>
                  {selectedCandidate.approved_by && <div>审核人：{selectedCandidate.approved_by}</div>}
                  {selectedCandidate.approved_at && <div>审核时间：{formatDate(selectedCandidate.approved_at)}</div>}
                  {selectedCandidate.knowledge_document_id && (
                    <div>知识库文档 ID：{selectedCandidate.knowledge_document_id}</div>
                  )}
                </div>
              </div>

              <div className="rounded-2xl border border-slate-800 bg-slate-950/50 p-4">
                <div className="text-xs font-medium text-slate-300">原问题</div>
                <div className="mt-2 whitespace-pre-wrap break-all text-sm text-slate-200">
                  {selectedCandidate.question || "-"}
                </div>
              </div>

              <div className="rounded-2xl border border-slate-800 bg-slate-950/50 p-4">
                <div className="text-xs font-medium text-slate-300">AI 初稿</div>
                <div className="mt-2 whitespace-pre-wrap break-all text-sm text-slate-200">
                  {selectedCandidate.ai_draft || "-"}
                </div>
              </div>

              <label className="grid gap-1 text-sm">
                <span className="text-slate-400">人工最终方案</span>
                <textarea
                  className="min-h-[220px] rounded-2xl border border-slate-700 bg-slate-950 px-3 py-3 text-sm"
                  value={draftFinalSolution}
                  onChange={(event) => setDraftFinalSolution(event.target.value)}
                  placeholder="在这里整理最终可入库的人工方案"
                />
              </label>

              <label className="grid gap-1 text-sm">
                <span className="text-slate-400">反馈备注</span>
                <textarea
                  className="min-h-[120px] rounded-2xl border border-slate-700 bg-slate-950 px-3 py-3 text-sm"
                  value={draftFeedbackComment}
                  onChange={(event) => setDraftFeedbackComment(event.target.value)}
                  placeholder="补充注意事项、限制条件或审核说明"
                />
              </label>

              <div className="rounded-2xl border border-slate-800 bg-slate-950/50 p-4 text-xs text-slate-400">
                <div>关联文档链接</div>
                <div className="mt-2 space-y-2">
                  {selectedCandidate.related_links.length > 0 ? (
                    selectedCandidate.related_links.map((item) => (
                      <a key={item} href={item} target="_blank" rel="noreferrer" className="block text-cyan-300">
                        {item}
                      </a>
                    ))
                  ) : (
                    <div>-</div>
                  )}
                </div>
              </div>

              <div className="flex flex-wrap gap-2">
                <button
                  className="rounded-xl border border-slate-700 px-4 py-2 text-sm text-slate-200 transition hover:border-slate-500 disabled:border-slate-800 disabled:text-slate-500"
                  onClick={() => {
                    void handleSubmit("save_edit");
                  }}
                  disabled={isSubmitting}
                >
                  {isSubmitting ? "保存中..." : "保存修改"}
                </button>
                <button
                  className="rounded-xl border border-emerald-400/40 px-4 py-2 text-sm text-emerald-200 transition hover:bg-emerald-400/10 disabled:border-slate-800 disabled:text-slate-500"
                  onClick={() => {
                    void handleSubmit("approve_and_publish");
                  }}
                  disabled={isSubmitting}
                >
                  {isSubmitting ? "处理中..." : "通过并入库"}
                </button>
              </div>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

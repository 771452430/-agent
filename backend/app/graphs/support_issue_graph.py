"""支持问题 Agent 的 LangGraph 编排。

这份图实现有一个明确目标：在不丢现有业务能力的前提下，
把支持问题 Agent 的三条主链路拆成真正可学习、可观察的 LangGraph 节点。

结构分四层：
1. `SupportIssueRunGraph` 负责一次“立即运行 / 定时运行”；
2. `SupportIssueRowGraph` 负责 run 内部的单条问题处理；
3. `SupportIssueFeedbackGraph` 负责全表反馈同步、通知检测和案例候选刷新；
4. `SupportIssueDigestGraph` 负责统计、邮件内容生成、发信和落库。

设计取舍：
- graph 只负责状态推进、节点分支和 trace；
- 真实副作用仍然复用 `SupportIssueService` 里的 helper；
- 这样改造后，你可以清楚看到“图的结构”，同时又不会把成熟业务判断拆坏。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal, TypedDict
from uuid import uuid4

from fastapi import HTTPException
from langgraph.graph import END, START, StateGraph

from ..schemas import (
    FeishuBitableFieldInfo,
    SupportIssueAgentConfig,
    SupportIssueCategoryStat,
    SupportIssueClassificationResult,
    SupportIssueDigestRun,
    SupportIssueDraftResult,
    SupportIssueEvidenceResult,
    SupportIssueFeedbackFact,
    SupportIssueFeedbackSnapshot,
    SupportIssueFeedbackSyncResponse,
    SupportIssueGraphTraceEvent,
    SupportIssueGraphTracePhase,
    SupportIssueGraphTraceStatus,
    SupportIssueReviewResult,
    SupportIssueRowResult,
    SupportIssueRun,
)

if TYPE_CHECKING:
    from ..services.support_issue_service import SupportIssueService


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# 这些常量与 service 里的业务语义保持一致。
# graph 侧重复声明，是为了避免 service -> graph 的循环 import。
PROCESSING_PROGRESS_VALUE = "分析中"
DONE_PROGRESS_VALUE = "AI分析完成"
MANUAL_REVIEW_PROGRESS_VALUE = "待人工确认"
FAILED_PROGRESS_VALUE = "失败待重试"
HUMAN_CONFIRMED_PROGRESS_VALUE = "人工确认完成"
NO_HIT_MESSAGE = "未检索到相关知识，已标记待人工确认，请人工补充处理。"
NO_HIT_MESSAGE_KEYWORD = "未检索到相关知识"
LOW_CONFIDENCE_THRESHOLD = 0.65
FEEDBACK_ACCEPTED = "直接采纳"
FEEDBACK_REVISED_ACCEPTED = "修改后采纳"
FEEDBACK_REJECTED = "驳回"


class SupportIssueRowGraphState(TypedDict, total=False):
    """单条支持问题处理图的状态。"""

    run_id: str
    agent: SupportIssueAgentConfig
    record: dict[str, Any]
    resolved_fields: dict[str, FeishuBitableFieldInfo]
    historical_cases: list[dict[str, str]]
    scope_type: str
    scope_id: str | None
    link_is_url_like: bool
    graph_trace: list[SupportIssueGraphTraceEvent]
    fields: dict[str, Any]
    record_id: str
    source_record_id: str
    source_table_id: str
    source_table_name: str
    source_bitable_url: str
    question: str
    module_value: str
    feedback_snapshot: SupportIssueFeedbackSnapshot | None
    fallback_category: str
    composed_query: str
    category: str
    similar_cases: list[dict[str, str]]
    similar_case_context: str
    classification_result: SupportIssueClassificationResult | None
    evidence_result: SupportIssueEvidenceResult | None
    draft_result: SupportIssueDraftResult | None
    review_result: SupportIssueReviewResult | None
    next_step: Literal["classify", "draft", "finalize", "no_hit", "question_empty", "write"]
    retrieval_result: Any
    retrieval_hit_count: int
    solution: str
    related_link: str | None
    related_link_field_value: Any
    judge_status: str
    confidence_score: float
    judge_reason: str
    progress_value: str
    update_fields: dict[str, Any]
    row_result: SupportIssueRowResult
    needs_support_owner_notify: bool
    support_owner_solution: str
    failure_message_separator: str


class SupportIssueRunGraphState(TypedDict, total=False):
    """支持问题 run 图的状态。"""

    agent_id: str
    agent: SupportIssueAgentConfig
    run_id: str
    started_at: datetime
    graph_trace: list[SupportIssueGraphTraceEvent]
    table_contexts: list[dict[str, str]]
    raw_rows: list[dict[str, Any]]
    resolved_fields: dict[str, FeishuBitableFieldInfo]
    historical_cases: list[dict[str, str]]
    candidate_rows: list[dict[str, Any]]
    row_results: list[SupportIssueRowResult]
    run: SupportIssueRun
    feedback_response: SupportIssueFeedbackSyncResponse | None


class SupportIssueFeedbackPlan(TypedDict, total=False):
    """反馈同步图在单条记录上的中间计划。"""

    record_id: str
    source_record_id: str
    source_table_id: str
    source_table_name: str
    source_bitable_url: str
    fields: dict[str, Any]
    next_fact: SupportIssueFeedbackFact
    previous_fact: SupportIssueFeedbackFact | None
    current_snapshot: dict[str, object]
    previous_snapshot: dict[str, object]
    changed_fields: list[str]
    persisted_fact: SupportIssueFeedbackFact


class SupportIssueFeedbackGraphState(TypedDict, total=False):
    """反馈同步图的状态。"""

    agent_id: str
    agent: SupportIssueAgentConfig
    synced_at: datetime
    graph_trace: list[SupportIssueGraphTraceEvent]
    table_contexts: list[dict[str, str]]
    raw_rows: list[dict[str, Any]]
    resolved_fields: dict[str, FeishuBitableFieldInfo]
    plans: list[SupportIssueFeedbackPlan]
    fact_upsert_count: int
    history_appended_count: int
    candidate_created_count: int
    candidate_updated_count: int
    response: SupportIssueFeedbackSyncResponse


class SupportIssueDigestGraphState(TypedDict, total=False):
    """digest 图的状态。"""

    agent_id: str
    agent: SupportIssueAgentConfig
    trigger_source: Literal["manual", "scheduled"]
    started_at: datetime
    graph_trace: list[SupportIssueGraphTraceEvent]
    feedback_response: SupportIssueFeedbackSyncResponse | None
    period_start: datetime
    period_end: datetime
    facts: list[SupportIssueFeedbackFact]
    candidates: list[Any]
    facts_in_period: list[SupportIssueFeedbackFact]
    candidates_in_period: list[Any]
    approved_candidates_in_period: list[Any]
    digest_items: list[dict[str, object]]
    digest_run: SupportIssueDigestRun
    email_body: str
    email_html_body: str


class _SupportIssueGraphBase:
    """支持问题 graph 的公共辅助逻辑。"""

    def __init__(self, runtime: SupportIssueService) -> None:
        self.runtime = runtime

    def _preview_value(self, value: Any) -> object:
        """把复杂对象压缩成适合 trace 展示的轻量摘要。"""

        if isinstance(value, datetime):
            return value.isoformat()
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            compact = value.strip()
            return compact if len(compact) <= 240 else compact[:239] + "…"
        if isinstance(value, list):
            return [self._preview_value(item) for item in value[:5]]
        if isinstance(value, dict):
            preview: dict[str, object] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= 8:
                    preview["..."] = "truncated"
                    break
                preview[str(key)] = self._preview_value(item)
            return preview
        return str(value)[:240]

    def _normalize_payload_preview(self, payload_preview: dict[str, object] | None) -> dict[str, object]:
        normalized: dict[str, object] = {}
        for key, value in (payload_preview or {}).items():
            normalized[str(key)] = self._preview_value(value)
        return normalized

    def _append_trace(
        self,
        *,
        trace: list[SupportIssueGraphTraceEvent],
        node: str,
        phase: SupportIssueGraphTracePhase,
        status: SupportIssueGraphTraceStatus,
        started_at: datetime,
        ended_at: datetime,
        message: str = "",
        record_id: str | None = None,
        payload_preview: dict[str, object] | None = None,
    ) -> list[SupportIssueGraphTraceEvent]:
        """返回一份追加了当前节点事件的新 trace 列表。"""

        next_trace = list(trace)
        next_trace.append(
            SupportIssueGraphTraceEvent(
                node=node,
                phase=phase,
                status=status,
                started_at=started_at,
                ended_at=ended_at,
                message=message,
                record_id=record_id,
                payload_preview=self._normalize_payload_preview(payload_preview),
            )
        )
        return next_trace

    def _run_traced_node(
        self,
        *,
        state: dict[str, Any],
        node: str,
        phase: SupportIssueGraphTracePhase,
        callback: Any,
        record_id: str | None = None,
    ) -> dict[str, Any]:
        """统一包装节点执行与 trace 记录。"""

        started_at = _utc_now()
        base_trace = list(state.get("graph_trace", []))
        updates = callback(state) or {}
        status = updates.pop("_trace_status", "success")
        message = str(updates.pop("_trace_message", "") or "")
        payload_preview = updates.pop("_trace_payload_preview", None)
        ended_at = _utc_now()
        updates["graph_trace"] = self._append_trace(
            trace=base_trace,
            node=node,
            phase=phase,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            message=message,
            record_id=record_id,
            payload_preview=payload_preview,
        )
        return updates


class SupportIssueRowGraph(_SupportIssueGraphBase):
    """单条支持问题处理图。

    这一版把“单行处理”改成 supervisor + 多子 agent 结构：
    - supervisor 先做上下文准备与分支判断；
    - `classifier_agent` 负责分类与检索 query 校准；
    - `evidence_agent` 负责检索证据；
    - `draft_agent` 负责整理草稿答复；
    - `review_agent` 负责复核是否可以直接完成。

    飞书回写、支持人通知、最终收口仍保留为确定性节点，
    这样既能学到多 agent 拆分方式，也不会丢现有业务稳定性。
    """

    def __init__(self, runtime: SupportIssueService) -> None:
        super().__init__(runtime)
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(SupportIssueRowGraphState)
        builder.add_node("prepare_row_context", self.prepare_row_context)
        builder.add_node("handle_empty_question", self.handle_empty_question)
        builder.add_node("classifier_agent", self.classifier_agent)
        builder.add_node("evidence_agent", self.evidence_agent)
        builder.add_node("handle_no_hit", self.handle_no_hit)
        builder.add_node("draft_agent", self.draft_agent)
        builder.add_node("review_agent", self.review_agent)
        builder.add_node("write_row", self.write_row)
        builder.add_node("notify_support_owner", self.notify_support_owner)
        builder.add_node("finalize_row", self.finalize_row)

        builder.add_edge(START, "prepare_row_context")
        builder.add_conditional_edges(
            "prepare_row_context",
            self._route_after_prepare_row_context,
            {
                "finalize": "finalize_row",
                "question_empty": "handle_empty_question",
                "classify": "classifier_agent",
            },
        )
        builder.add_edge("classifier_agent", "evidence_agent")
        builder.add_conditional_edges(
            "evidence_agent",
            self._route_after_evidence_agent,
            {
                "no_hit": "handle_no_hit",
                "draft": "draft_agent",
                "write": "write_row",
            },
        )
        builder.add_edge("handle_empty_question", "write_row")
        builder.add_edge("handle_no_hit", "write_row")
        builder.add_edge("draft_agent", "review_agent")
        builder.add_edge("review_agent", "write_row")
        builder.add_edge("write_row", "notify_support_owner")
        builder.add_edge("notify_support_owner", "finalize_row")
        builder.add_edge("finalize_row", END)
        return builder.compile()

    def _route_after_prepare_row_context(
        self,
        state: SupportIssueRowGraphState,
    ) -> Literal["classify", "finalize", "question_empty"]:
        return state.get("next_step", "classify")

    def _route_after_evidence_agent(
        self,
        state: SupportIssueRowGraphState,
    ) -> Literal["draft", "no_hit", "write"]:
        return state.get("next_step", "write")

    def _build_row_result(
        self,
        state: SupportIssueRowGraphState,
        *,
        status: Literal["generated", "manual_review", "no_hit", "failed"],
        solution: str,
        related_link: str | None,
        message: str,
        retrieval_hit_count: int,
        confidence_score: float,
        judge_status: str,
        judge_reason: str,
        question: str | None = None,
    ) -> SupportIssueRowResult:
        """按统一口径构造单行结果，避免每个节点各自拼字段。"""

        return SupportIssueRowResult(
            record_id=state.get("record_id", ""),
            source_record_id=state.get("source_record_id", ""),
            source_table_id=state.get("source_table_id", ""),
            source_table_name=state.get("source_table_name", ""),
            source_bitable_url=state.get("source_bitable_url", ""),
            question=state.get("question", "") if question is None else question,
            status=status,
            solution=solution,
            related_link=related_link,
            message=message,
            retrieval_hit_count=retrieval_hit_count,
            confidence_score=confidence_score,
            judge_status=judge_status,
            judge_reason=judge_reason,
            question_category=state.get("category", ""),
            similar_case_count=len(state.get("similar_cases", [])),
            feedback_snapshot=state.get("feedback_snapshot"),
            classification_result=state.get("classification_result"),
            evidence_result=state.get("evidence_result"),
            draft_result=state.get("draft_result"),
            review_result=state.get("review_result"),
        )

    def _fallback_classification_result(self, state: SupportIssueRowGraphState) -> SupportIssueClassificationResult:
        """在 LLM 分类不可用时，回退到原有规则分类。"""

        return SupportIssueClassificationResult(
            category=state.get("fallback_category", "") or state.get("category", "") or "FAQ",
            composed_query=state.get("composed_query", "") or state.get("question", ""),
            reasoning="已回退到内置规则分类。",
            supervisor_notes="保留现有 query 组合方式。",
        )

    def _classify_issue(self, state: SupportIssueRowGraphState) -> SupportIssueClassificationResult:
        llm_service = getattr(self.runtime, "llm_service", None)
        fallback = self._fallback_classification_result(state)
        if llm_service is None or not hasattr(llm_service, "classify_support_issue"):
            return fallback
        try:
            result = llm_service.classify_support_issue(
                question=state.get("question", ""),
                composed_query=state.get("composed_query", ""),
                module_value=state.get("module_value", ""),
                fallback_category=fallback.category,
                similar_case_context=state.get("similar_case_context", ""),
                model_config=state["agent"].model_settings,
            )
            return SupportIssueClassificationResult.model_validate(result)
        except Exception:
            return fallback

    def _draft_issue_solution(self, state: SupportIssueRowGraphState, *, retrieval_summary: str) -> SupportIssueDraftResult:
        llm_service = getattr(self.runtime, "llm_service", None)
        fallback = SupportIssueDraftResult(
            solution=retrieval_summary.strip(),
            reasoning="已直接采用检索总结作为草稿答案。",
            used_similar_case_count=len(state.get("similar_cases", [])),
        )
        if llm_service is None or not hasattr(llm_service, "draft_support_solution"):
            return fallback
        try:
            result = llm_service.draft_support_solution(
                question=state.get("question", ""),
                category=state.get("category", ""),
                retrieval_summary=retrieval_summary,
                retrieval_hit_count=state.get("retrieval_hit_count", 0),
                similar_case_context=state.get("similar_case_context", ""),
                similar_case_count=len(state.get("similar_cases", [])),
                model_config=state["agent"].model_settings,
            )
            return SupportIssueDraftResult.model_validate(result)
        except Exception:
            return fallback

    def _review_issue_solution(
        self,
        state: SupportIssueRowGraphState,
        *,
        fallback_judge_status: str,
        fallback_confidence_score: float,
        fallback_reason: str,
    ) -> SupportIssueReviewResult:
        llm_service = getattr(self.runtime, "llm_service", None)
        normalized_status = "pass" if fallback_judge_status == "pass" else "manual_review"
        fallback = SupportIssueReviewResult(
            judge_status=normalized_status,
            confidence_score=round(max(0.0, min(1.0, fallback_confidence_score)), 4),
            judge_reason=fallback_reason,
            progress_value=DONE_PROGRESS_VALUE if normalized_status == "pass" else MANUAL_REVIEW_PROGRESS_VALUE,
            reviewer_notes="已回退到内置复核判断。",
        )
        if llm_service is None or not hasattr(llm_service, "review_support_solution"):
            return fallback
        evidence_result = state.get("evidence_result")
        evidence_summary = evidence_result.evidence_summary if evidence_result is not None else ""
        try:
            result = llm_service.review_support_solution(
                question=state.get("question", ""),
                category=state.get("category", ""),
                draft_solution=state.get("solution", ""),
                retrieval_hit_count=state.get("retrieval_hit_count", 0),
                evidence_summary=evidence_summary,
                fallback_judge_status=fallback_judge_status,
                fallback_confidence_score=fallback_confidence_score,
                fallback_reason=fallback_reason,
                model_config=state["agent"].model_settings,
            )
            return SupportIssueReviewResult.model_validate(result)
        except Exception:
            return fallback

    def prepare_row_context(self, state: SupportIssueRowGraphState) -> dict[str, Any]:
        """Supervisor 节点：准备单行上下文，并先把飞书状态打到“分析中”。"""

        def callback(current_state: SupportIssueRowGraphState) -> dict[str, Any]:
            record = current_state["record"]
            fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
            source_record_id = self.runtime._extract_record_id(record)
            source_table_id = self.runtime._row_table_id(record, agent=current_state["agent"])
            source_table_name = self.runtime._row_table_name(record)
            source_bitable_url = self.runtime._row_bitable_url(record, agent=current_state["agent"])
            record_id = self.runtime._build_scoped_record_id(
                table_id=source_table_id,
                record_id=source_record_id,
            )
            resolved_fields = current_state["resolved_fields"]
            question = self.runtime._stringify_field_value(fields.get(resolved_fields["question"].field_name))
            module_value = self.runtime._stringify_field_value(fields.get(resolved_fields["module"].field_name))
            feedback_snapshot = self.runtime._extract_feedback_snapshot(current_state["agent"], fields)
            composed_query = self.runtime._compose_query(question=question, fields=fields)
            fallback_category = self.runtime._classify_question(composed_query if composed_query != "" else question)
            similar_cases = self.runtime._select_similar_cases(
                query=composed_query or question,
                cases=current_state.get("historical_cases", []),
            )
            similar_case_context = self.runtime._build_similar_case_context(similar_cases)

            base_updates = {
                "fields": fields,
                "record_id": record_id,
                "source_record_id": source_record_id,
                "source_table_id": source_table_id,
                "source_table_name": source_table_name,
                "source_bitable_url": source_bitable_url,
                "question": question,
                "module_value": module_value,
                "feedback_snapshot": feedback_snapshot,
                "fallback_category": fallback_category,
                "composed_query": composed_query,
                "category": fallback_category,
                "similar_cases": similar_cases,
                "similar_case_context": similar_case_context,
                "needs_support_owner_notify": False,
            }

            if source_record_id == "":
                row_result = self._build_row_result(
                    base_updates,
                    status="failed",
                    solution="",
                    related_link=None,
                    message="当前行缺少 record_id，无法回写飞书。",
                    retrieval_hit_count=0,
                    confidence_score=0.0,
                    judge_status="failed",
                    judge_reason="缺少 record_id。",
                    question=question,
                )
                return {
                    **base_updates,
                    "row_result": row_result,
                    "next_step": "finalize",
                    "_trace_status": "failed",
                    "_trace_message": "缺少 record_id，已跳过回写。",
                    "_trace_payload_preview": {"question": question, "category": fallback_category},
                }

            try:
                self.runtime._update_row_fields(
                    current_state["agent"],
                    record_id=record_id,
                    fields={resolved_fields["progress"].field_name: PROCESSING_PROGRESS_VALUE},
                )
            except Exception as exc:
                row_result = self._build_row_result(
                    base_updates,
                    status="failed",
                    solution="",
                    related_link=None,
                    message=f"写入处理中状态失败：{exc}",
                    retrieval_hit_count=0,
                    confidence_score=0.0,
                    judge_status="failed",
                    judge_reason=f"写入处理中失败：{exc}",
                    question=question,
                )
                return {
                    **base_updates,
                    "row_result": row_result,
                    "next_step": "finalize",
                    "_trace_status": "failed",
                    "_trace_message": f"写入分析中失败：{exc}",
                    "_trace_payload_preview": {"record_id": record_id},
                }

            next_step: Literal["classify", "question_empty"] = "question_empty" if question == "" else "classify"
            return {
                **base_updates,
                "next_step": next_step,
                "_trace_message": "Supervisor 已完成行上下文准备，并把飞书记录标记为分析中。",
                "_trace_payload_preview": {
                    "record_id": record_id,
                    "question_empty": question == "",
                    "similar_case_count": len(similar_cases),
                },
            }

        return self._run_traced_node(
            state=state,
            node="prepare_row_context",
            phase="row",
            callback=callback,
            record_id=state.get("record_id"),
        )

    def handle_empty_question(self, state: SupportIssueRowGraphState) -> dict[str, Any]:
        """问题为空时，准备失败回写内容，后续统一交给 write 节点处理。"""

        def callback(current_state: SupportIssueRowGraphState) -> dict[str, Any]:
            resolved_fields = current_state["resolved_fields"]
            update_fields = self.runtime._build_runtime_update_fields(
                (resolved_fields["answer"], "生成失败：问题列为空，请补充后重试。"),
                (resolved_fields["link"], self.runtime._empty_link_field_value(url_like_field=current_state["link_is_url_like"])),
                (resolved_fields["confidence"], 0.0),
                (resolved_fields["hit_count"], 0),
                (resolved_fields["progress"], FAILED_PROGRESS_VALUE),
            )
            row_result = self._build_row_result(
                current_state,
                status="failed",
                solution="",
                related_link=None,
                message="问题列为空，无法生成解决方案。",
                retrieval_hit_count=0,
                confidence_score=0.0,
                judge_status="failed",
                judge_reason="问题列为空。",
                question="",
            )
            return {
                "update_fields": update_fields,
                "row_result": row_result,
                "failure_message_separator": " ",
                "needs_support_owner_notify": False,
                "_trace_status": "failed",
                "_trace_message": "问题列为空，已准备失败回写。",
                "_trace_payload_preview": {"record_id": current_state["record_id"]},
            }

        return self._run_traced_node(
            state=state,
            node="handle_empty_question",
            phase="row",
            callback=callback,
            record_id=state.get("record_id"),
        )

    def classifier_agent(self, state: SupportIssueRowGraphState) -> dict[str, Any]:
        """分类子 agent：确定问题类别，并校准用于检索的组合 query。"""

        def callback(current_state: SupportIssueRowGraphState) -> dict[str, Any]:
            classification_result = self._classify_issue(current_state)
            category = classification_result.category.strip() or current_state.get("fallback_category", "FAQ")
            composed_query = classification_result.composed_query.strip() or current_state.get("composed_query", "")
            return {
                "classification_result": classification_result,
                "category": category,
                "composed_query": composed_query,
                "_trace_message": "分类子 agent 已完成类别判断和检索 query 校准。",
                "_trace_payload_preview": {
                    "record_id": current_state["record_id"],
                    "category": category,
                    "reasoning": classification_result.reasoning,
                },
            }

        return self._run_traced_node(
            state=state,
            node="classifier_agent",
            phase="row",
            callback=callback,
            record_id=state.get("record_id"),
        )

    def evidence_agent(self, state: SupportIssueRowGraphState) -> dict[str, Any]:
        """证据子 agent：执行 scoped RAG 检索，并提炼证据摘要。"""

        def callback(current_state: SupportIssueRowGraphState) -> dict[str, Any]:
            try:
                system_prompt = self.runtime._compose_system_prompt(current_state["category"])
                if current_state.get("similar_case_context", "") != "":
                    system_prompt = (
                        system_prompt
                        + "以下历史已采纳案例仅作为辅助参考，不能覆盖知识依据；"
                        + current_state["similar_case_context"]
                    )
                retrieval_result = self.runtime.retrieval_service.run(
                    query=current_state["composed_query"],
                    scope_type=current_state["scope_type"],
                    scope_id=current_state.get("scope_id"),
                    model_config=current_state["agent"].model_settings,
                    system_prompt=system_prompt,
                    retrieval_profile="support_issue",
                    query_bundle_context={
                        "question": current_state["question"],
                        "module_value": current_state.get("module_value", ""),
                        "category": current_state.get("category", ""),
                        "similar_case_context": current_state.get("similar_case_context", ""),
                    },
                )
                retrieval_hit_count = len(getattr(retrieval_result, "citations", []) or [])
                evidence_summary = str(getattr(retrieval_result, "summary", "") or "").strip()
                retrieval_debug = getattr(retrieval_result, "debug", None)
                evidence_result = SupportIssueEvidenceResult(
                    retrieval_hit_count=retrieval_hit_count,
                    evidence_summary=evidence_summary,
                    no_hit=retrieval_hit_count == 0,
                    source_note="证据来自 scoped RAG 检索结果。",
                )
                next_step: Literal["draft", "no_hit"] = "no_hit" if retrieval_hit_count == 0 else "draft"
                return {
                    "retrieval_result": retrieval_result,
                    "retrieval_hit_count": retrieval_hit_count,
                    "evidence_result": evidence_result,
                    "next_step": next_step,
                    "_trace_message": "证据子 agent 已完成检索。",
                    "_trace_payload_preview": {
                        "record_id": current_state["record_id"],
                        "retrieval_hit_count": retrieval_hit_count,
                        "query_variants": (
                            [item.query for item in retrieval_debug.query_bundle.query_variants[:4]]
                            if retrieval_debug is not None
                            else []
                        ),
                        "candidate_count": retrieval_debug.candidate_count if retrieval_debug is not None else 0,
                        "selected_count": retrieval_debug.selected_count if retrieval_debug is not None else 0,
                    },
                }
            except Exception as exc:
                error_text = str(exc).strip() or "未知错误"
                failure_solution = f"生成失败：{error_text[:240]}"
                resolved_fields = current_state["resolved_fields"]
                evidence_result = SupportIssueEvidenceResult(
                    retrieval_hit_count=0,
                    evidence_summary="",
                    no_hit=False,
                    source_note=f"检索异常：{error_text[:120]}",
                )
                update_fields = self.runtime._build_runtime_update_fields(
                    (resolved_fields["answer"], failure_solution),
                    (resolved_fields["link"], self.runtime._empty_link_field_value(url_like_field=current_state["link_is_url_like"])),
                    (resolved_fields["confidence"], 0.0),
                    (resolved_fields["hit_count"], 0),
                    (resolved_fields["progress"], FAILED_PROGRESS_VALUE),
                )
                row_result = self._build_row_result(
                    dict(current_state, evidence_result=evidence_result),
                    status="failed",
                    solution=failure_solution,
                    related_link=None,
                    message=error_text,
                    retrieval_hit_count=0,
                    confidence_score=0.0,
                    judge_status="failed",
                    judge_reason=error_text[:200],
                )
                return {
                    "evidence_result": evidence_result,
                    "update_fields": update_fields,
                    "row_result": row_result,
                    "needs_support_owner_notify": False,
                    "failure_message_separator": "；",
                    "next_step": "write",
                    "_trace_status": "failed",
                    "_trace_message": error_text,
                    "_trace_payload_preview": {"record_id": current_state["record_id"]},
                }

        return self._run_traced_node(
            state=state,
            node="evidence_agent",
            phase="row",
            callback=callback,
            record_id=state.get("record_id"),
        )

    def handle_no_hit(self, state: SupportIssueRowGraphState) -> dict[str, Any]:
        """无命中时，把记录转到待人工确认。"""

        def callback(current_state: SupportIssueRowGraphState) -> dict[str, Any]:
            resolved_fields = current_state["resolved_fields"]
            update_fields = self.runtime._build_runtime_update_fields(
                (resolved_fields["answer"], NO_HIT_MESSAGE),
                (resolved_fields["link"], self.runtime._empty_link_field_value(url_like_field=current_state["link_is_url_like"])),
                (resolved_fields["confidence"], 0.0),
                (resolved_fields["hit_count"], 0),
                (resolved_fields["progress"], MANUAL_REVIEW_PROGRESS_VALUE),
            )
            row_result = self._build_row_result(
                current_state,
                status="no_hit",
                solution=NO_HIT_MESSAGE,
                related_link=None,
                message="未检索到可用知识，已标记待人工确认。",
                retrieval_hit_count=0,
                confidence_score=0.0,
                judge_status="no_hit",
                judge_reason="未命中知识，已转人工确认。",
            )
            return {
                "update_fields": update_fields,
                "row_result": row_result,
                "needs_support_owner_notify": True,
                "support_owner_solution": NO_HIT_MESSAGE,
                "_trace_message": "未命中知识，已准备转人工确认。",
                "_trace_payload_preview": {"record_id": current_state["record_id"]},
            }

        return self._run_traced_node(
            state=state,
            node="handle_no_hit",
            phase="row",
            callback=callback,
            record_id=state.get("record_id"),
        )

    def draft_agent(self, state: SupportIssueRowGraphState) -> dict[str, Any]:
        """草稿子 agent：把检索证据整理成可回写的答复草稿。"""

        def callback(current_state: SupportIssueRowGraphState) -> dict[str, Any]:
            retrieval_result = current_state["retrieval_result"]
            retrieval_summary = str(getattr(retrieval_result, "summary", "") or "").strip()
            draft_result = self._draft_issue_solution(current_state, retrieval_summary=retrieval_summary)
            solution = draft_result.solution.strip()
            related_link = self.runtime._join_related_document_links(
                retrieval_result,
                url_like_field=current_state["link_is_url_like"],
            )
            related_link_field_value = self.runtime._build_link_field_value(
                retrieval_result,
                url_like_field=current_state["link_is_url_like"],
            )
            return {
                "draft_result": draft_result,
                "solution": solution,
                "related_link": related_link,
                "related_link_field_value": related_link_field_value,
                "_trace_message": "草稿子 agent 已完成答复整理。",
                "_trace_payload_preview": {
                    "record_id": current_state["record_id"],
                    "solution_preview": solution,
                },
            }

        return self._run_traced_node(
            state=state,
            node="draft_agent",
            phase="row",
            callback=callback,
            record_id=state.get("record_id"),
        )

    def review_agent(self, state: SupportIssueRowGraphState) -> dict[str, Any]:
        """复核子 agent：判断草稿是否可直接完成，或需要转人工确认。"""

        def callback(current_state: SupportIssueRowGraphState) -> dict[str, Any]:
            fallback_judge_status, fallback_confidence_score, fallback_reason = self.runtime._judge_solution(
                question=current_state["question"],
                summary=current_state["solution"],
                retrieval_hit_count=current_state["retrieval_hit_count"],
            )
            review_result = self._review_issue_solution(
                current_state,
                fallback_judge_status=fallback_judge_status,
                fallback_confidence_score=fallback_confidence_score,
                fallback_reason=fallback_reason,
            )
            judge_status = review_result.judge_status
            confidence_score = round(review_result.confidence_score, 4)
            judge_reason = review_result.judge_reason
            progress_value = review_result.progress_value or (
                DONE_PROGRESS_VALUE if judge_status == "pass" else MANUAL_REVIEW_PROGRESS_VALUE
            )
            resolved_fields = current_state["resolved_fields"]
            update_fields = self.runtime._build_runtime_update_fields(
                (resolved_fields["answer"], current_state["solution"]),
                (resolved_fields["link"], current_state["related_link_field_value"]),
                (resolved_fields["confidence"], confidence_score),
                (resolved_fields["hit_count"], current_state["retrieval_hit_count"]),
                (resolved_fields["progress"], progress_value),
            )
            row_result = self._build_row_result(
                dict(current_state, review_result=review_result),
                status="generated" if judge_status == "pass" else "manual_review",
                solution=current_state["solution"],
                related_link=current_state.get("related_link"),
                message=(
                    "已生成解决方案并回写飞书。"
                    if judge_status == "pass"
                    else f"已生成草稿答案，因置信度偏低转人工确认：{judge_reason}"
                ),
                retrieval_hit_count=current_state["retrieval_hit_count"],
                confidence_score=confidence_score,
                judge_status=judge_status,
                judge_reason=judge_reason,
            )
            return {
                "review_result": review_result,
                "judge_status": judge_status,
                "confidence_score": confidence_score,
                "judge_reason": judge_reason,
                "progress_value": progress_value,
                "update_fields": update_fields,
                "row_result": row_result,
                "needs_support_owner_notify": judge_status != "pass",
                "support_owner_solution": current_state["solution"],
                "_trace_message": "复核子 agent 已完成草稿复核。",
                "_trace_payload_preview": {
                    "record_id": current_state["record_id"],
                    "judge_status": judge_status,
                    "confidence_score": confidence_score,
                },
            }

        return self._run_traced_node(
            state=state,
            node="review_agent",
            phase="row",
            callback=callback,
            record_id=state.get("record_id"),
        )

    def _fallback_existing_failed_write(
        self,
        state: SupportIssueRowGraphState,
        *,
        write_error: Exception,
    ) -> SupportIssueRowResult:
        """处理“本来就在失败分支中，又连失败信息都回写不成功”的兜底。"""

        base_message = state["row_result"].message
        separator = state.get("failure_message_separator", "；")
        progress_field_name = state["resolved_fields"]["progress"].field_name
        message = base_message
        try:
            self.runtime._mark_record_progress_only(
                state["agent"],
                record_id=state["record_id"],
                progress_field_name=progress_field_name,
                progress_value=FAILED_PROGRESS_VALUE,
            )
            message = f"{base_message}{separator}详细失败信息回写失败，已仅更新回复进度为失败待重试：{write_error}"
        except Exception as progress_exc:
            message = (
                f"{base_message}{separator}回写失败待重试状态也失败：{write_error}"
                f"；进度单独回写也失败：{progress_exc}"
            )
        return state["row_result"].model_copy(update={"message": message})

    def write_row(self, state: SupportIssueRowGraphState) -> dict[str, Any]:
        """统一执行飞书回写。

        这里把成功、无命中、问题为空、运行异常几类回写都收口到一个节点，
        方便前端把“真正写飞书”的动作独立展示出来。
        """

        def callback(current_state: SupportIssueRowGraphState) -> dict[str, Any]:
            update_fields = current_state.get("update_fields", {})
            if current_state.get("record_id", "") == "" or len(update_fields) == 0:
                return {
                    "_trace_status": "skipped",
                    "_trace_message": "当前分支没有需要执行的飞书回写。",
                }

            try:
                self.runtime._update_row_fields(
                    current_state["agent"],
                    record_id=current_state["record_id"],
                    fields=update_fields,
                )
                return {
                    "_trace_message": "飞书回写成功。",
                    "_trace_payload_preview": {
                        "record_id": current_state["record_id"],
                        "field_count": len(update_fields),
                    },
                }
            except Exception as exc:
                # 如果当前行本来就在失败分支上，保持原来的失败语义，只补一个 progress-only 兜底。
                if current_state["row_result"].status == "failed":
                    row_result = self._fallback_existing_failed_write(current_state, write_error=exc)
                    return {
                        "row_result": row_result,
                        "needs_support_owner_notify": False,
                        "_trace_status": "failed",
                        "_trace_message": str(exc),
                        "_trace_payload_preview": {"record_id": current_state["record_id"]},
                    }

                # 否则说明原本是 no_hit / generated / manual_review，但最终写表失败。
                error_text = str(exc).strip() or "未知错误"
                failure_solution = f"生成失败：{error_text[:240]}"
                resolved_fields = current_state["resolved_fields"]
                message = error_text
                try:
                    self.runtime._update_row_fields(
                        current_state["agent"],
                        record_id=current_state["record_id"],
                        fields=self.runtime._build_runtime_update_fields(
                            (resolved_fields["answer"], failure_solution),
                            (
                                resolved_fields["link"],
                                self.runtime._empty_link_field_value(url_like_field=current_state["link_is_url_like"]),
                            ),
                            (resolved_fields["confidence"], 0.0),
                            (resolved_fields["hit_count"], 0),
                            (resolved_fields["progress"], FAILED_PROGRESS_VALUE),
                        ),
                    )
                except Exception as update_exc:
                    try:
                        self.runtime._mark_record_progress_only(
                            current_state["agent"],
                            record_id=current_state["record_id"],
                            progress_field_name=resolved_fields["progress"].field_name,
                            progress_value=FAILED_PROGRESS_VALUE,
                        )
                        message = f"{message}；详细失败信息回写失败，已仅更新回复进度为失败待重试：{update_exc}"
                    except Exception as progress_exc:
                        message = (
                            f"{message}；回写失败待重试状态也失败：{update_exc}"
                            f"；进度单独回写也失败：{progress_exc}"
                        )

                row_result = self._build_row_result(
                    current_state,
                    status="failed",
                    solution=failure_solution,
                    related_link=None,
                    message=message,
                    retrieval_hit_count=0,
                    confidence_score=0.0,
                    judge_status="failed",
                    judge_reason=error_text[:200],
                )
                return {
                    "row_result": row_result,
                    "needs_support_owner_notify": False,
                    "_trace_status": "failed",
                    "_trace_message": error_text,
                    "_trace_payload_preview": {"record_id": current_state["record_id"]},
                }

        return self._run_traced_node(
            state=state,
            node="write_row",
            phase="row",
            callback=callback,
            record_id=state.get("record_id"),
        )

    def notify_support_owner(self, state: SupportIssueRowGraphState) -> dict[str, Any]:
        """在行结果转人工确认时，通知对应支持人。"""

        def callback(current_state: SupportIssueRowGraphState) -> dict[str, Any]:
            row_result = current_state["row_result"]
            if not current_state.get("needs_support_owner_notify", False) or row_result.status not in {
                "manual_review",
                "no_hit",
            }:
                return {
                    "_trace_status": "skipped",
                    "_trace_message": "当前行不需要发送支持人通知。",
                }

            self.runtime._notify_support_owner_for_manual_review(
                agent=current_state["agent"],
                run_id=current_state["run_id"],
                record_id=current_state["record_id"],
                source_bitable_url=current_state.get("source_bitable_url", current_state["agent"].feishu_bitable_url),
                source_table_name=current_state.get("source_table_name", ""),
                question=current_state["question"],
                module_value=current_state.get("module_value", ""),
                solution=current_state.get("support_owner_solution", ""),
            )
            return {
                "_trace_message": "支持人通知已触发。",
                "_trace_payload_preview": {
                    "record_id": current_state["record_id"],
                    "module_value": current_state.get("module_value", ""),
                },
            }

        return self._run_traced_node(
            state=state,
            node="notify_support_owner",
            phase="row",
            callback=callback,
            record_id=state.get("record_id"),
        )

    def finalize_row(self, state: SupportIssueRowGraphState) -> dict[str, Any]:
        """把完整 trace 挂回到单行结果上。"""

        started_at = _utc_now()
        base_trace = list(state.get("graph_trace", []))
        row_result = state.get("row_result")
        if row_result is None:
            row_result = self._build_row_result(
                state,
                status="failed",
                solution="",
                related_link=None,
                message="单行处理异常结束，未生成有效结果。",
                retrieval_hit_count=0,
                confidence_score=0.0,
                judge_status="failed",
                judge_reason="graph 未产出 row_result。",
            )
        ended_at = _utc_now()
        trace = self._append_trace(
            trace=base_trace,
            node="finalize_row",
            phase="row",
            status="success",
            started_at=started_at,
            ended_at=ended_at,
            message="单行轨迹整理完成。",
            record_id=state.get("record_id"),
        )
        return {
            "graph_trace": trace,
            "row_result": row_result.model_copy(update={"graph_trace": trace}),
        }


class SupportIssueFeedbackGraph(_SupportIssueGraphBase):
    """反馈同步图。"""

    def __init__(self, runtime: SupportIssueService) -> None:
        super().__init__(runtime)
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(SupportIssueFeedbackGraphState)
        builder.add_node("read_bitable_rows", self.read_bitable_rows)
        builder.add_node("build_feedback_facts", self.build_feedback_facts)
        builder.add_node("compare_snapshots", self.compare_snapshots)
        builder.add_node("upsert_facts", self.upsert_facts)
        builder.add_node("append_history", self.append_history)
        builder.add_node("backfill_support_owner_notifications", self.backfill_support_owner_notifications)
        builder.add_node("detect_registrant_confirmation_completed", self.detect_registrant_confirmation_completed)
        builder.add_node("refresh_case_candidates", self.refresh_case_candidates)
        builder.add_node("summarize_sync", self.summarize_sync)

        builder.add_edge(START, "read_bitable_rows")
        builder.add_edge("read_bitable_rows", "build_feedback_facts")
        builder.add_edge("build_feedback_facts", "compare_snapshots")
        builder.add_edge("compare_snapshots", "upsert_facts")
        builder.add_edge("upsert_facts", "append_history")
        builder.add_edge("append_history", "backfill_support_owner_notifications")
        builder.add_edge("backfill_support_owner_notifications", "detect_registrant_confirmation_completed")
        builder.add_edge("detect_registrant_confirmation_completed", "refresh_case_candidates")
        builder.add_edge("refresh_case_candidates", "summarize_sync")
        builder.add_edge("summarize_sync", END)
        return builder.compile()

    def read_bitable_rows(self, state: SupportIssueFeedbackGraphState) -> dict[str, Any]:
        """读取飞书全表数据。"""

        def callback(current_state: SupportIssueFeedbackGraphState) -> dict[str, Any]:
            agent = current_state["agent"]
            try:
                raw_rows, table_contexts = self.runtime._list_all_agent_rows(agent)
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"飞书反馈同步失败：{exc}") from exc
            return {
                "table_contexts": table_contexts,
                "raw_rows": raw_rows,
                "_trace_message": "飞书反馈数据读取完成。",
                "_trace_payload_preview": {"table_count": len(table_contexts), "row_count": len(raw_rows)},
            }

        return self._run_traced_node(
            state=state,
            node="read_bitable_rows",
            phase="feedback",
            callback=callback,
        )

    def build_feedback_facts(self, state: SupportIssueFeedbackGraphState) -> dict[str, Any]:
        """把原始飞书行转成结构化 feedback fact 计划。"""

        def callback(current_state: SupportIssueFeedbackGraphState) -> dict[str, Any]:
            agent = current_state["agent"]
            resolved_fields = self.runtime._resolve_runtime_field_mapping(agent, records=current_state.get("raw_rows", []))
            plans: list[SupportIssueFeedbackPlan] = []
            for row in current_state.get("raw_rows", []):
                source_record_id = self.runtime._extract_record_id(row)
                source_table_id = self.runtime._row_table_id(row, agent=agent)
                source_table_name = self.runtime._row_table_name(row)
                source_bitable_url = self.runtime._row_bitable_url(row, agent=agent)
                record_id = self.runtime._build_scoped_record_id(table_id=source_table_id, record_id=source_record_id)
                fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
                if source_record_id == "" or not isinstance(fields, dict):
                    continue
                next_fact = self.runtime._build_feedback_fact(
                    agent=agent,
                    record_id=record_id,
                    fields=fields,
                    resolved_fields=resolved_fields,
                    synced_at=current_state["synced_at"],
                    source_bitable_url=source_bitable_url,
                )
                plans.append(
                    {
                        "record_id": record_id,
                        "source_record_id": source_record_id,
                        "source_table_id": source_table_id,
                        "source_table_name": source_table_name,
                        "source_bitable_url": source_bitable_url,
                        "fields": fields,
                        "next_fact": next_fact,
                        "previous_fact": self.runtime.support_issue_store.get_feedback_fact(agent.id, record_id),
                    }
                )
            return {
                "resolved_fields": resolved_fields,
                "plans": plans,
                "_trace_message": "反馈事实构建完成。",
                "_trace_payload_preview": {"plan_count": len(plans)},
            }

        return self._run_traced_node(
            state=state,
            node="build_feedback_facts",
            phase="feedback",
            callback=callback,
        )

    def compare_snapshots(self, state: SupportIssueFeedbackGraphState) -> dict[str, Any]:
        """比较新旧快照，确定 history 与完成态通知的触发条件。"""

        def callback(current_state: SupportIssueFeedbackGraphState) -> dict[str, Any]:
            plans: list[SupportIssueFeedbackPlan] = []
            for plan in current_state.get("plans", []):
                next_fact = plan["next_fact"]
                previous_fact = plan.get("previous_fact")
                current_snapshot = self.runtime._feedback_fact_snapshot_dict(next_fact)
                previous_snapshot = (
                    self.runtime._feedback_fact_snapshot_dict(previous_fact)
                    if previous_fact is not None
                    else {}
                )
                changed_fields = self.runtime._diff_feedback_fact_snapshots(previous_snapshot, current_snapshot)
                if previous_fact is not None and len(changed_fields) == 0:
                    next_fact.updated_at = previous_fact.updated_at
                    next_fact.created_at = previous_fact.created_at
                plans.append(
                    {
                        **plan,
                        "current_snapshot": current_snapshot,
                        "previous_snapshot": previous_snapshot,
                        "changed_fields": changed_fields,
                    }
                )
            return {
                "plans": plans,
                "_trace_message": "反馈快照比较完成。",
                "_trace_payload_preview": {
                    "changed_record_count": sum(1 for item in plans if len(item.get("changed_fields", [])) > 0),
                },
            }

        return self._run_traced_node(
            state=state,
            node="compare_snapshots",
            phase="feedback",
            callback=callback,
        )

    def upsert_facts(self, state: SupportIssueFeedbackGraphState) -> dict[str, Any]:
        """把当前最新 fact 落到平台库。"""

        def callback(current_state: SupportIssueFeedbackGraphState) -> dict[str, Any]:
            plans: list[SupportIssueFeedbackPlan] = []
            for plan in current_state.get("plans", []):
                persisted_fact = self.runtime.support_issue_store.upsert_feedback_fact(plan["next_fact"])
                plans.append({**plan, "persisted_fact": persisted_fact})
            return {
                "plans": plans,
                "fact_upsert_count": len(plans),
                "_trace_message": "反馈事实 upsert 完成。",
                "_trace_payload_preview": {"fact_upsert_count": len(plans)},
            }

        return self._run_traced_node(
            state=state,
            node="upsert_facts",
            phase="feedback",
            callback=callback,
        )

    def append_history(self, state: SupportIssueFeedbackGraphState) -> dict[str, Any]:
        """只对真正发生变化的历史写入轨迹。"""

        def callback(current_state: SupportIssueFeedbackGraphState) -> dict[str, Any]:
            history_appended_count = 0
            for plan in current_state.get("plans", []):
                previous_fact = plan.get("previous_fact")
                changed_fields = plan.get("changed_fields", [])
                if previous_fact is None or len(changed_fields) == 0:
                    continue
                self.runtime.support_issue_store.append_feedback_history(
                    agent_id=current_state["agent"].id,
                    record_id=plan["record_id"],
                    changed_fields=changed_fields,
                    previous_snapshot=plan["previous_snapshot"],
                    current_snapshot=plan["current_snapshot"],
                    changed_at=current_state["synced_at"],
                )
                history_appended_count += 1
            return {
                "history_appended_count": history_appended_count,
                "_trace_message": "反馈历史追加完成。",
                "_trace_payload_preview": {"history_appended_count": history_appended_count},
            }

        return self._run_traced_node(
            state=state,
            node="append_history",
            phase="feedback",
            callback=callback,
        )

    def backfill_support_owner_notifications(self, state: SupportIssueFeedbackGraphState) -> dict[str, Any]:
        """确保所有“待人工确认”记录都有支持人通知。"""

        def callback(current_state: SupportIssueFeedbackGraphState) -> dict[str, Any]:
            for plan in current_state.get("plans", []):
                self.runtime._backfill_support_owner_notification_if_needed(
                    agent=current_state["agent"],
                    record_id=plan["record_id"],
                    fields=plan["fields"],
                    resolved_fields=current_state["resolved_fields"],
                    fact=plan["persisted_fact"],
                    synced_at=current_state["synced_at"],
                    source_bitable_url=plan.get("source_bitable_url", current_state["agent"].feishu_bitable_url),
                    source_table_name=plan.get("source_table_name", ""),
                )
            return {
                "_trace_message": "支持人补通知检测完成。",
                "_trace_payload_preview": {"plan_count": len(current_state.get("plans", []))},
            }

        return self._run_traced_node(
            state=state,
            node="backfill_support_owner_notifications",
            phase="feedback",
            callback=callback,
        )

    def detect_registrant_confirmation_completed(self, state: SupportIssueFeedbackGraphState) -> dict[str, Any]:
        """只在进度从“非完成态”首次变成“人工确认完成”时通知登记人。"""

        def callback(current_state: SupportIssueFeedbackGraphState) -> dict[str, Any]:
            notified_count = 0
            for plan in current_state.get("plans", []):
                previous_fact = plan.get("previous_fact")
                persisted_fact = plan["persisted_fact"]
                if previous_fact is None:
                    continue
                if previous_fact.progress_value == HUMAN_CONFIRMED_PROGRESS_VALUE:
                    continue
                if persisted_fact.progress_value != HUMAN_CONFIRMED_PROGRESS_VALUE:
                    continue
                self.runtime._notify_registrants_for_confirmation_completed(
                    agent=current_state["agent"],
                    record_id=plan["record_id"],
                    fields=plan["fields"],
                    resolved_fields=current_state["resolved_fields"],
                    fact=persisted_fact,
                    progress_changed_at=current_state["synced_at"],
                    source_bitable_url=plan.get("source_bitable_url", current_state["agent"].feishu_bitable_url),
                    source_table_name=plan.get("source_table_name", ""),
                )
                notified_count += 1
            return {
                "_trace_message": "登记人完成态通知检测完成。",
                "_trace_payload_preview": {"registrant_notified_count": notified_count},
            }

        return self._run_traced_node(
            state=state,
            node="detect_registrant_confirmation_completed",
            phase="feedback",
            callback=callback,
        )

    def refresh_case_candidates(self, state: SupportIssueFeedbackGraphState) -> dict[str, Any]:
        """根据人工反馈生成或刷新案例候选。"""

        def callback(current_state: SupportIssueFeedbackGraphState) -> dict[str, Any]:
            candidate_created_count = 0
            candidate_updated_count = 0
            agent = current_state["agent"]
            for plan in current_state.get("plans", []):
                persisted_fact = plan["persisted_fact"]
                current_candidate = self.runtime.support_issue_store.get_case_candidate_by_record(agent.id, plan["record_id"])
                next_candidate = self.runtime._feedback_fact_to_candidate(persisted_fact)
                should_create_candidate = agent.case_review_enabled and self.runtime._should_create_case_candidate(persisted_fact)

                if current_candidate is None:
                    if not should_create_candidate:
                        continue
                    self.runtime.support_issue_store.upsert_case_candidate(
                        next_candidate,
                        reset_to_pending_review=True,
                    )
                    candidate_created_count += 1
                    continue

                candidate_changed = self.runtime._case_candidate_payload_changed(current_candidate, next_candidate)
                if candidate_changed:
                    if current_candidate.status == "approved":
                        self.runtime._delete_case_candidate_document(current_candidate)
                    self.runtime.support_issue_store.upsert_case_candidate(
                        next_candidate,
                        reset_to_pending_review=True,
                    )
                    candidate_updated_count += 1
            return {
                "candidate_created_count": candidate_created_count,
                "candidate_updated_count": candidate_updated_count,
                "_trace_message": "案例候选创建/刷新完成。",
                "_trace_payload_preview": {
                    "candidate_created_count": candidate_created_count,
                    "candidate_updated_count": candidate_updated_count,
                },
            }

        return self._run_traced_node(
            state=state,
            node="refresh_case_candidates",
            phase="feedback",
            callback=callback,
        )

    def summarize_sync(self, state: SupportIssueFeedbackGraphState) -> dict[str, Any]:
        """产出同步响应，并把 trace 一并挂回去。"""

        started_at = _utc_now()
        summary = (
            f"本次同步读取 {len(state.get('raw_rows', []))} 行；"
            f"更新反馈事实 {state.get('fact_upsert_count', 0)} 条，追加历史 {state.get('history_appended_count', 0)} 条，"
            f"新增候选 {state.get('candidate_created_count', 0)} 条，刷新候选 {state.get('candidate_updated_count', 0)} 条。"
        )
        response = SupportIssueFeedbackSyncResponse(
            agent_id=state["agent"].id,
            synced_row_count=len(state.get("raw_rows", [])),
            fact_upsert_count=state.get("fact_upsert_count", 0),
            history_appended_count=state.get("history_appended_count", 0),
            candidate_created_count=state.get("candidate_created_count", 0),
            candidate_updated_count=state.get("candidate_updated_count", 0),
            summary=summary,
        )
        ended_at = _utc_now()
        trace = self._append_trace(
            trace=list(state.get("graph_trace", [])),
            node="summarize_sync",
            phase="feedback",
            status="success",
            started_at=started_at,
            ended_at=ended_at,
            message="反馈同步结果汇总完成。",
            payload_preview={
                "fact_upsert_count": state.get("fact_upsert_count", 0),
                "history_appended_count": state.get("history_appended_count", 0),
            },
        )
        return {
            "graph_trace": trace,
            "response": response.model_copy(update={"graph_trace": trace}),
        }


class SupportIssueDigestGraph(_SupportIssueGraphBase):
    """digest 图。"""

    def __init__(self, runtime: SupportIssueService, feedback_graph: SupportIssueFeedbackGraph) -> None:
        super().__init__(runtime)
        self.feedback_graph = feedback_graph
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(SupportIssueDigestGraphState)
        builder.add_node("run_feedback_sync", self.run_feedback_sync)
        builder.add_node("load_facts_candidates", self.load_facts_candidates)
        builder.add_node("calculate_statistics", self.calculate_statistics)
        builder.add_node("generate_digest_items", self.generate_digest_items)
        builder.add_node("generate_email_bodies", self.generate_email_bodies)
        builder.add_node("send_email", self.send_email)
        builder.add_node("persist_digest_run", self.persist_digest_run)

        builder.add_edge(START, "run_feedback_sync")
        builder.add_edge("run_feedback_sync", "load_facts_candidates")
        builder.add_edge("load_facts_candidates", "calculate_statistics")
        builder.add_edge("calculate_statistics", "generate_digest_items")
        builder.add_edge("generate_digest_items", "generate_email_bodies")
        builder.add_edge("generate_email_bodies", "send_email")
        builder.add_edge("send_email", "persist_digest_run")
        builder.add_edge("persist_digest_run", END)
        return builder.compile()

    def run_feedback_sync(self, state: SupportIssueDigestGraphState) -> dict[str, Any]:
        """digest 统计前先做一轮反馈同步，保证统计口径最新。"""

        def callback(current_state: SupportIssueDigestGraphState) -> dict[str, Any]:
            feedback_state: SupportIssueFeedbackGraphState = {
                "agent_id": current_state["agent"].id,
                "agent": current_state["agent"],
                "synced_at": _utc_now(),
                "graph_trace": [],
            }
            final_state = self.feedback_graph.graph.invoke(feedback_state)
            response = final_state.get("response")
            if not isinstance(response, SupportIssueFeedbackSyncResponse):
                raise RuntimeError("反馈同步图未返回有效结果。")
            return {
                "feedback_response": response,
                "_trace_message": "digest 前置反馈同步完成。",
                "_trace_payload_preview": {"feedback_summary": response.summary},
            }

        return self._run_traced_node(
            state=state,
            node="run_feedback_sync",
            phase="digest",
            callback=callback,
        )

    def load_facts_candidates(self, state: SupportIssueDigestGraphState) -> dict[str, Any]:
        """加载 digest 统计所需的 facts 与 candidates。"""

        def callback(current_state: SupportIssueDigestGraphState) -> dict[str, Any]:
            period_start, period_end = self.runtime._build_digest_period(current_state["started_at"])
            facts = self.runtime.support_issue_store.list_feedback_facts(current_state["agent"].id)
            candidates = self.runtime.support_issue_store.list_case_candidates(current_state["agent"].id)
            return {
                "period_start": period_start,
                "period_end": period_end,
                "facts": facts,
                "candidates": candidates,
                "_trace_message": "digest 统计源数据加载完成。",
                "_trace_payload_preview": {
                    "fact_count": len(facts),
                    "candidate_count": len(candidates),
                },
            }

        return self._run_traced_node(
            state=state,
            node="load_facts_candidates",
            phase="digest",
            callback=callback,
        )

    def calculate_statistics(self, state: SupportIssueDigestGraphState) -> dict[str, Any]:
        """计算 digest 核心统计并构建 digest_run。"""

        def callback(current_state: SupportIssueDigestGraphState) -> dict[str, Any]:
            facts_in_period = [fact for fact in current_state.get("facts", []) if fact.updated_at >= current_state["period_start"]]
            candidates_in_period = [
                candidate for candidate in current_state.get("candidates", []) if candidate.updated_at >= current_state["period_start"]
            ]
            approved_candidates_in_period = [
                candidate
                for candidate in candidates_in_period
                if candidate.status == "approved"
                and candidate.approved_at is not None
                and candidate.approved_at >= current_state["period_start"]
            ]

            generated_count = sum(1 for fact in facts_in_period if fact.progress_value == DONE_PROGRESS_VALUE)
            no_hit_facts = [fact for fact in facts_in_period if fact.retrieval_hit_count == 0 and NO_HIT_MESSAGE_KEYWORD in fact.ai_solution]
            no_hit_record_ids = {fact.record_id for fact in no_hit_facts}
            manual_review_count = sum(
                1
                for fact in facts_in_period
                if fact.progress_value == MANUAL_REVIEW_PROGRESS_VALUE and fact.record_id not in no_hit_record_ids
            )
            no_hit_count = len(no_hit_facts)
            failed_count = sum(1 for fact in facts_in_period if fact.progress_value == FAILED_PROGRESS_VALUE)
            total_processed_count = generated_count + manual_review_count + no_hit_count + failed_count

            acceptance_count = sum(1 for fact in facts_in_period if fact.feedback_result == FEEDBACK_ACCEPTED)
            revised_acceptance_count = sum(1 for fact in facts_in_period if fact.feedback_result == FEEDBACK_REVISED_ACCEPTED)
            rejected_count = sum(1 for fact in facts_in_period if fact.feedback_result == FEEDBACK_REJECTED)
            analyzed_feedback_total = acceptance_count + revised_acceptance_count + rejected_count
            low_confidence_count = sum(
                1 for fact in facts_in_period if fact.confidence_score < LOW_CONFIDENCE_THRESHOLD and fact.progress_value != ""
            )

            category_counter = Counter(
                fact.question_category for fact in facts_in_period if fact.question_category.strip() != ""
            )
            top_categories = [
                SupportIssueCategoryStat(category=category, count=count)
                for category, count in category_counter.most_common(5)
            ]

            no_hit_topic_counter = Counter(
                self.runtime._question_topic(fact.question)
                for fact in no_hit_facts
                if fact.question.strip() != ""
            )
            top_no_hit_topics = [topic for topic, _count in no_hit_topic_counter.most_common(5)]

            highlight_samples = [
                f"{self.runtime._question_topic(fact.question)}｜进度={fact.progress_value or '未知'}｜分类={fact.question_category or '未分类'}"
                for fact in sorted(facts_in_period, key=lambda item: item.updated_at, reverse=True)[:5]
            ]

            knowledge_gap_suggestions: list[str] = []
            if no_hit_count > 0:
                knowledge_gap_suggestions.append("无命中问题仍然存在，建议优先补齐高频无命中主题对应的知识文档。")
            if revised_acceptance_count > 0:
                knowledge_gap_suggestions.append("“修改后采纳”仍有样本，建议将人工补充步骤沉淀为标准案例模板。")
            if rejected_count > 0:
                knowledge_gap_suggestions.append("存在驳回样本，建议复盘该类问题的检索词拼接与回答模板。")
            if len(approved_candidates_in_period) == 0 and len(candidates_in_period) > 0:
                knowledge_gap_suggestions.append("已有候选案例尚未入库，建议尽快完成审核，提升后续复用率。")

            digest_run = SupportIssueDigestRun(
                id=str(uuid4()),
                agent_id=current_state["agent"].id,
                status="success",
                trigger_source=current_state["trigger_source"],
                started_at=current_state["started_at"],
                ended_at=_utc_now(),
                period_start=current_state["period_start"],
                period_end=current_state["period_end"],
                recipient_emails=current_state["agent"].digest_recipient_emails,
                email_sent=False,
                email_subject="",
                summary=self.runtime._build_digest_summary(
                    total_processed_count=total_processed_count,
                    generated_count=generated_count,
                    manual_review_count=manual_review_count,
                    no_hit_count=no_hit_count,
                    failed_count=failed_count,
                    acceptance_count=acceptance_count,
                    revised_acceptance_count=revised_acceptance_count,
                    rejected_count=rejected_count,
                    new_candidate_count=len(candidates_in_period),
                    approved_candidate_count=len(approved_candidates_in_period),
                ),
                error_message=None,
                total_processed_count=total_processed_count,
                generated_count=generated_count,
                manual_review_count=manual_review_count,
                no_hit_count=no_hit_count,
                failed_count=failed_count,
                acceptance_count=acceptance_count,
                revised_acceptance_count=revised_acceptance_count,
                rejected_count=rejected_count,
                acceptance_rate=self.runtime._safe_rate(
                    acceptance_count + revised_acceptance_count,
                    max(analyzed_feedback_total, 1),
                ),
                rejection_rate=self.runtime._safe_rate(rejected_count, max(analyzed_feedback_total, 1)),
                low_confidence_rate=self.runtime._safe_rate(low_confidence_count, max(total_processed_count, 1)),
                no_hit_rate=self.runtime._safe_rate(no_hit_count, max(total_processed_count, 1)),
                manual_rewrite_rate=self.runtime._safe_rate(revised_acceptance_count, max(analyzed_feedback_total, 1)),
                top_categories=top_categories,
                top_no_hit_topics=top_no_hit_topics,
                highlight_samples=highlight_samples,
                knowledge_gap_suggestions=knowledge_gap_suggestions,
                new_candidate_count=len(candidates_in_period),
                approved_candidate_count=len(approved_candidates_in_period),
            )
            digest_run.email_subject = (
                f"【支持问题 Agent 立即汇总】{current_state['agent'].name}"
                if digest_run.trigger_source == "manual"
                else f"【支持问题 Agent 周期汇总】{current_state['agent'].name}"
            )
            return {
                "facts_in_period": facts_in_period,
                "candidates_in_period": candidates_in_period,
                "approved_candidates_in_period": approved_candidates_in_period,
                "digest_run": digest_run,
                "_trace_message": "digest 统计计算完成。",
                "_trace_payload_preview": {
                    "total_processed_count": total_processed_count,
                    "generated_count": generated_count,
                    "manual_review_count": manual_review_count,
                },
            }

        return self._run_traced_node(
            state=state,
            node="calculate_statistics",
            phase="digest",
            callback=callback,
        )

    def generate_digest_items(self, state: SupportIssueDigestGraphState) -> dict[str, Any]:
        """整理 digest 的明细项，便于后续审计和追溯。"""

        def callback(current_state: SupportIssueDigestGraphState) -> dict[str, Any]:
            digest_items: list[dict[str, object]] = []
            for fact in current_state.get("facts_in_period", []):
                digest_items.append(
                    {
                        "record_id": fact.record_id,
                        "item_type": "feedback_fact",
                        "title": self.runtime._question_topic(fact.question),
                        "payload": {
                            "progress_value": fact.progress_value,
                            "feedback_result": fact.feedback_result,
                            "question_category": fact.question_category,
                        },
                    }
                )
            for candidate in current_state.get("candidates_in_period", []):
                digest_items.append(
                    {
                        "record_id": candidate.record_id,
                        "candidate_id": candidate.id,
                        "item_type": "case_candidate",
                        "title": self.runtime._question_topic(candidate.question),
                        "payload": {
                            "status": candidate.status,
                            "question_category": candidate.question_category,
                        },
                    }
                )
            return {
                "digest_items": digest_items,
                "_trace_message": "digest 明细项生成完成。",
                "_trace_payload_preview": {"digest_item_count": len(digest_items)},
            }

        return self._run_traced_node(
            state=state,
            node="generate_digest_items",
            phase="digest",
            callback=callback,
        )

    def generate_email_bodies(self, state: SupportIssueDigestGraphState) -> dict[str, Any]:
        """生成纯文本与 HTML 两套邮件正文。"""

        def callback(current_state: SupportIssueDigestGraphState) -> dict[str, Any]:
            body, html_body = self.runtime._build_digest_email_bodies(
                agent=current_state["agent"],
                digest_run=current_state["digest_run"],
            )
            return {
                "email_body": body,
                "email_html_body": html_body,
                "_trace_message": "digest 邮件内容生成完成。",
                "_trace_payload_preview": {"subject": current_state["digest_run"].email_subject},
            }

        return self._run_traced_node(
            state=state,
            node="generate_email_bodies",
            phase="digest",
            callback=callback,
        )

    def send_email(self, state: SupportIssueDigestGraphState) -> dict[str, Any]:
        """执行 SMTP 发信，但失败不阻断 digest 落库。"""

        def callback(current_state: SupportIssueDigestGraphState) -> dict[str, Any]:
            digest_run = current_state["digest_run"]
            try:
                self.runtime.mail_service.send_email(
                    recipient_emails=current_state["agent"].digest_recipient_emails,
                    subject=digest_run.email_subject,
                    body=current_state["email_body"],
                    html_body=current_state["email_html_body"],
                )
                digest_run = digest_run.model_copy(update={"email_sent": True})
                trace_status: SupportIssueGraphTraceStatus = "success"
                trace_message = "digest 邮件发送成功。"
            except Exception as exc:
                digest_run = digest_run.model_copy(update={"status": "failed", "error_message": str(exc)})
                trace_status = "failed"
                trace_message = str(exc)
            return {
                "digest_run": digest_run,
                "_trace_status": trace_status,
                "_trace_message": trace_message,
                "_trace_payload_preview": {"recipient_count": len(current_state["agent"].digest_recipient_emails)},
            }

        return self._run_traced_node(
            state=state,
            node="send_email",
            phase="digest",
            callback=callback,
        )

    def persist_digest_run(self, state: SupportIssueDigestGraphState) -> dict[str, Any]:
        """把 digest run 和 trace 一起落库。"""

        started_at = _utc_now()
        digest_run = state["digest_run"].model_copy(update={"ended_at": _utc_now()})
        trace = self._append_trace(
            trace=list(state.get("graph_trace", [])),
            node="persist_digest_run",
            phase="digest",
            status="success",
            started_at=started_at,
            ended_at=_utc_now(),
            message="digest 记录已落库。",
            payload_preview={"status": digest_run.status},
        )
        persisted_run = digest_run.model_copy(update={"graph_trace": trace})
        self.runtime.support_issue_store.record_digest_run(
            agent_id=state["agent"].id,
            run=persisted_run,
            items=state.get("digest_items", []),
        )
        return {
            "graph_trace": trace,
            "digest_run": persisted_run,
        }


class SupportIssueRunGraph(_SupportIssueGraphBase):
    """支持问题 run 图。"""

    def __init__(
        self,
        runtime: SupportIssueService,
        row_graph: SupportIssueRowGraph,
        feedback_graph: SupportIssueFeedbackGraph,
    ) -> None:
        super().__init__(runtime)
        self.row_graph = row_graph
        self.feedback_graph = feedback_graph
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(SupportIssueRunGraphState)
        builder.add_node("load_agent_and_context", self.load_agent_and_context)
        builder.add_node("read_bitable_rows", self.read_bitable_rows)
        builder.add_node("parse_runtime_field_mapping", self.parse_runtime_field_mapping)
        builder.add_node("collect_historical_cases", self.collect_historical_cases)
        builder.add_node("filter_candidate_rows", self.filter_candidate_rows)
        builder.add_node("process_candidate_rows", self.process_candidate_rows)
        builder.add_node("summarize_run", self.summarize_run)
        builder.add_node("trigger_feedback_graph", self.trigger_feedback_graph)
        builder.add_node("record_run", self.record_run)

        builder.add_edge(START, "load_agent_and_context")
        builder.add_edge("load_agent_and_context", "read_bitable_rows")
        builder.add_conditional_edges(
            "read_bitable_rows",
            self._route_after_read_rows,
            {"resolve": "parse_runtime_field_mapping", "record": "record_run"},
        )
        builder.add_edge("parse_runtime_field_mapping", "collect_historical_cases")
        builder.add_edge("collect_historical_cases", "filter_candidate_rows")
        builder.add_edge("filter_candidate_rows", "process_candidate_rows")
        builder.add_edge("process_candidate_rows", "summarize_run")
        builder.add_edge("summarize_run", "trigger_feedback_graph")
        builder.add_edge("trigger_feedback_graph", "record_run")
        builder.add_edge("record_run", END)
        return builder.compile()

    def _route_after_read_rows(self, state: SupportIssueRunGraphState) -> Literal["resolve", "record"]:
        return "record" if state.get("run") is not None else "resolve"

    def load_agent_and_context(self, state: SupportIssueRunGraphState) -> dict[str, Any]:
        """准备 run 的 Agent、run_id、started_at。"""

        def callback(current_state: SupportIssueRunGraphState) -> dict[str, Any]:
            agent = current_state.get("agent")
            if agent is None:
                agent = self.runtime.get_agent(current_state["agent_id"])
            return {
                "agent": agent,
                "run_id": current_state.get("run_id") or str(uuid4()),
                "started_at": current_state.get("started_at") or _utc_now(),
                "_trace_message": "运行上下文加载完成。",
                "_trace_payload_preview": {"agent_id": agent.id, "agent_name": agent.name},
            }

        return self._run_traced_node(
            state=state,
            node="load_agent_and_context",
            phase="run",
            callback=callback,
        )

    def read_bitable_rows(self, state: SupportIssueRunGraphState) -> dict[str, Any]:
        """读取飞书问题表；如果整表读取失败，直接生成 failed run。"""

        def callback(current_state: SupportIssueRunGraphState) -> dict[str, Any]:
            agent = current_state["agent"]
            try:
                raw_rows, table_contexts = self.runtime._list_all_agent_rows(agent)
                return {
                    "table_contexts": table_contexts,
                    "raw_rows": raw_rows,
                    "_trace_message": "飞书问题表读取完成。",
                    "_trace_payload_preview": {"table_count": len(table_contexts), "row_count": len(raw_rows)},
                }
            except Exception as exc:
                failed_run = SupportIssueRun(
                    id=current_state["run_id"],
                    agent_id=agent.id,
                    status="failed",
                    started_at=current_state["started_at"],
                    ended_at=_utc_now(),
                    fetched_row_count=0,
                    processed_row_count=0,
                    generated_count=0,
                    manual_review_count=0,
                    no_hit_count=0,
                    failed_count=0,
                    summary="飞书表格读取失败，未进入待分析筛选与检索回写链路。",
                    error_message=str(exc),
                    row_results=[],
                )
                return {
                    "run": failed_run,
                    "_trace_status": "failed",
                    "_trace_message": str(exc),
                }

        return self._run_traced_node(
            state=state,
            node="read_bitable_rows",
            phase="run",
            callback=callback,
        )

    def parse_runtime_field_mapping(self, state: SupportIssueRunGraphState) -> dict[str, Any]:
        """解析运行时字段映射。"""

        def callback(current_state: SupportIssueRunGraphState) -> dict[str, Any]:
            resolved_fields = self.runtime._resolve_runtime_field_mapping(
                current_state["agent"],
                records=current_state.get("raw_rows", []),
            )
            return {
                "resolved_fields": resolved_fields,
                "_trace_message": "运行时字段映射解析完成。",
                "_trace_payload_preview": {
                    "question_field": resolved_fields["question"].field_name,
                    "progress_field": resolved_fields["progress"].field_name,
                },
            }

        return self._run_traced_node(
            state=state,
            node="parse_runtime_field_mapping",
            phase="run",
            callback=callback,
        )

    def collect_historical_cases(self, state: SupportIssueRunGraphState) -> dict[str, Any]:
        """收集历史已采纳案例，供相似案例提示使用。"""

        def callback(current_state: SupportIssueRunGraphState) -> dict[str, Any]:
            resolved_fields = current_state["resolved_fields"]
            historical_cases = self.runtime._collect_historical_cases(
                rows=current_state.get("raw_rows", []),
                question_field_name=resolved_fields["question"].field_name,
                agent=current_state["agent"],
            )
            for approved_case in self.runtime.support_issue_store.list_approved_case_candidates(current_state["agent"].id):
                if approved_case.question.strip() == "" or approved_case.final_solution.strip() == "":
                    continue
                historical_cases.append(
                    {
                        "question": approved_case.question,
                        "solution": approved_case.final_solution,
                        "feedback_result": approved_case.feedback_result or FEEDBACK_ACCEPTED,
                    }
                )
            return {
                "historical_cases": historical_cases,
                "_trace_message": "历史案例收集完成。",
                "_trace_payload_preview": {"historical_case_count": len(historical_cases)},
            }

        return self._run_traced_node(
            state=state,
            node="collect_historical_cases",
            phase="run",
            callback=callback,
        )

    def filter_candidate_rows(self, state: SupportIssueRunGraphState) -> dict[str, Any]:
        """筛选待分析 / 失败待重试的记录。"""

        def callback(current_state: SupportIssueRunGraphState) -> dict[str, Any]:
            progress_field_name = current_state["resolved_fields"]["progress"].field_name
            candidate_rows: list[dict[str, Any]] = []
            for item in current_state.get("raw_rows", []):
                fields = item.get("fields")
                if not isinstance(fields, dict):
                    continue
                if self.runtime._row_needs_processing(progress_field_name, fields):
                    candidate_rows.append(item)
            return {
                "candidate_rows": candidate_rows,
                "_trace_message": "待处理记录筛选完成。",
                "_trace_payload_preview": {
                    "candidate_row_count": len(candidate_rows),
                    "progress_field_name": progress_field_name,
                },
            }

        return self._run_traced_node(
            state=state,
            node="filter_candidate_rows",
            phase="run",
            callback=callback,
        )

    def _build_crashed_row_result(
        self,
        current_state: SupportIssueRunGraphState,
        *,
        row: dict[str, Any],
        exc: Exception,
    ) -> SupportIssueRowResult:
        """兜底处理 row graph 自身异常，避免整次 run 被单行拖垮。"""

        fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
        source_record_id = self.runtime._extract_record_id(row)
        source_table_id = self.runtime._row_table_id(row, agent=current_state["agent"])
        record_id = self.runtime._build_scoped_record_id(table_id=source_table_id, record_id=source_record_id)
        question = self.runtime._stringify_field_value(fields.get(current_state["resolved_fields"]["question"].field_name))
        category = self.runtime._classify_question(question)
        trace = [
            SupportIssueGraphTraceEvent(
                node="row_graph_crashed",
                phase="row",
                status="failed",
                started_at=_utc_now(),
                ended_at=_utc_now(),
                message=str(exc),
                record_id=record_id or None,
                payload_preview={},
            )
        ]
        return SupportIssueRowResult(
            record_id=record_id,
            source_record_id=source_record_id,
            source_table_id=source_table_id,
            source_table_name=self.runtime._row_table_name(row),
            source_bitable_url=self.runtime._row_bitable_url(row, agent=current_state["agent"]),
            question=question,
            status="failed",
            solution=f"生成失败：{str(exc)[:240]}",
            related_link=None,
            message=str(exc),
            retrieval_hit_count=0,
            confidence_score=0.0,
            judge_status="failed",
            judge_reason=str(exc)[:200],
            question_category=category,
            similar_case_count=0,
            feedback_snapshot=self.runtime._extract_feedback_snapshot(current_state["agent"], fields),
            graph_trace=trace,
        )

    def process_candidate_rows(self, state: SupportIssueRunGraphState) -> dict[str, Any]:
        """串行执行单行子图，并保留每行 trace。"""

        def callback(current_state: SupportIssueRunGraphState) -> dict[str, Any]:
            candidate_rows = current_state.get("candidate_rows", [])
            if len(candidate_rows) == 0:
                return {
                    "row_results": [],
                    "_trace_status": "skipped",
                    "_trace_message": "当前没有待处理记录，跳过行级子图。",
                }

            scope_type, scope_id = self.runtime._normalize_scope(
                current_state["agent"].knowledge_scope_type,
                current_state["agent"].knowledge_scope_id,
            )
            link_is_url_like = self.runtime._is_url_like_field(current_state["resolved_fields"]["link"])
            row_results: list[SupportIssueRowResult] = []
            for row in candidate_rows:
                row_state: SupportIssueRowGraphState = {
                    "run_id": current_state["run_id"],
                    "agent": current_state["agent"],
                    "record": row,
                    "resolved_fields": current_state["resolved_fields"],
                    "historical_cases": current_state.get("historical_cases", []),
                    "scope_type": scope_type,
                    "scope_id": scope_id,
                    "link_is_url_like": link_is_url_like,
                    "graph_trace": [],
                }
                try:
                    final_row_state = self.row_graph.graph.invoke(row_state)
                    row_result = final_row_state.get("row_result")
                    if isinstance(row_result, SupportIssueRowResult):
                        row_results.append(row_result)
                    else:
                        raise RuntimeError("单行图未返回 row_result。")
                except Exception as exc:
                    row_results.append(self._build_crashed_row_result(current_state, row=row, exc=exc))
            return {
                "row_results": row_results,
                "_trace_message": "行级子图处理完成。",
                "_trace_payload_preview": {"processed_row_count": len(row_results)},
            }

        return self._run_traced_node(
            state=state,
            node="process_candidate_rows",
            phase="run",
            callback=callback,
        )

    def summarize_run(self, state: SupportIssueRunGraphState) -> dict[str, Any]:
        """把行级结果汇总成一次 run。"""

        def callback(current_state: SupportIssueRunGraphState) -> dict[str, Any]:
            raw_rows = current_state.get("raw_rows", [])
            candidate_rows = current_state.get("candidate_rows", [])
            if len(candidate_rows) == 0:
                run = SupportIssueRun(
                    id=current_state["run_id"],
                    agent_id=current_state["agent"].id,
                    status="no_change",
                    started_at=current_state["started_at"],
                    ended_at=_utc_now(),
                    fetched_row_count=len(raw_rows),
                    processed_row_count=0,
                    generated_count=0,
                    manual_review_count=0,
                    no_hit_count=0,
                    failed_count=0,
                    summary=(
                        f"本轮读取 {len(raw_rows)} 行，当前没有“{current_state['resolved_fields']['progress'].field_name}”"
                        "为待分析或失败待重试的数据。"
                    ),
                    error_message=None,
                    row_results=[],
                )
                return {
                    "run": run,
                    "_trace_status": "skipped",
                    "_trace_message": "没有待处理记录，run 收口为 no_change。",
                }

            row_results = current_state.get("row_results", [])
            generated_count = sum(1 for item in row_results if item.status == "generated")
            manual_review_count = sum(1 for item in row_results if item.status == "manual_review")
            no_hit_count = sum(1 for item in row_results if item.status == "no_hit")
            failed_count = sum(1 for item in row_results if item.status == "failed")

            if failed_count > 0 and generated_count == 0 and manual_review_count == 0 and no_hit_count == 0:
                run_status = "failed"
            elif failed_count > 0:
                run_status = "partial_success"
            else:
                run_status = "success"

            run = SupportIssueRun(
                id=current_state["run_id"],
                agent_id=current_state["agent"].id,
                status=run_status,
                started_at=current_state["started_at"],
                ended_at=_utc_now(),
                fetched_row_count=len(raw_rows),
                processed_row_count=len(candidate_rows),
                generated_count=generated_count,
                manual_review_count=manual_review_count,
                no_hit_count=no_hit_count,
                failed_count=failed_count,
                summary=(
                    f"本轮读取 {len(raw_rows)} 行，命中待处理 {len(candidate_rows)} 行；"
                    f"已生成 {generated_count} 行，待人工确认 {manual_review_count} 行，"
                    f"无命中 {no_hit_count} 行，失败 {failed_count} 行。"
                ),
                error_message=None if failed_count == 0 else "部分或全部问题处理失败，请查看行级摘要。",
                row_results=row_results,
            )
            return {
                "run": run,
                "_trace_message": "run 结果汇总完成。",
                "_trace_payload_preview": {
                    "run_status": run_status,
                    "processed_row_count": len(candidate_rows),
                },
            }

        return self._run_traced_node(
            state=state,
            node="summarize_run",
            phase="run",
            callback=callback,
        )

    def trigger_feedback_graph(self, state: SupportIssueRunGraphState) -> dict[str, Any]:
        """run 结束后统一触发一次 feedback graph。

        这里刻意不把 feedback 失败升级成 run 失败，
        因为主链路的核心仍然是“读表 -> 检索 -> 回写”。
        """

        def callback(current_state: SupportIssueRunGraphState) -> dict[str, Any]:
            if len(current_state.get("raw_rows", [])) == 0 and current_state["run"].fetched_row_count == 0:
                return {
                    "_trace_status": "skipped",
                    "_trace_message": "飞书表读取失败，跳过 feedback graph。",
                }

            feedback_state: SupportIssueFeedbackGraphState = {
                "agent_id": current_state["agent"].id,
                "agent": current_state["agent"],
                "synced_at": _utc_now(),
                "graph_trace": [],
            }
            try:
                final_feedback_state = self.feedback_graph.graph.invoke(feedback_state)
                response = final_feedback_state.get("response")
                if isinstance(response, SupportIssueFeedbackSyncResponse):
                    return {
                        "feedback_response": response,
                        "_trace_message": "feedback graph 执行完成。",
                        "_trace_payload_preview": {"feedback_summary": response.summary},
                    }
                return {
                    "_trace_status": "failed",
                    "_trace_message": "feedback graph 未返回有效结果。",
                }
            except Exception as exc:
                return {
                    "_trace_status": "failed",
                    "_trace_message": str(exc),
                }

        return self._run_traced_node(
            state=state,
            node="trigger_feedback_graph",
            phase="run",
            callback=callback,
        )

    def record_run(self, state: SupportIssueRunGraphState) -> dict[str, Any]:
        """把 run 与 trace 一起落库。"""

        started_at = _utc_now()
        run = state["run"].model_copy(update={"ended_at": state["run"].ended_at or _utc_now()})
        trace = self._append_trace(
            trace=list(state.get("graph_trace", [])),
            node="record_run",
            phase="run",
            status="success",
            started_at=started_at,
            ended_at=_utc_now(),
            message="运行记录已落库。",
            payload_preview={"run_status": run.status},
        )
        persisted_run = run.model_copy(update={"graph_trace": trace})
        self.runtime.support_issue_store.record_run(state["agent"].id, persisted_run)
        return {
            "graph_trace": trace,
            "run": persisted_run,
        }

"""支持问题 Agent LangGraph 回归测试。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.graphs.support_issue_graph import (
    DONE_PROGRESS_VALUE,
    FEEDBACK_ACCEPTED,
    HUMAN_CONFIRMED_PROGRESS_VALUE,
    MANUAL_REVIEW_PROGRESS_VALUE,
    NO_HIT_MESSAGE,
    PROCESSING_PROGRESS_VALUE,
    SupportIssueDigestGraph,
    SupportIssueFeedbackGraph,
    SupportIssueRowGraph,
    SupportIssueRunGraph,
)
from app.schemas import (
    FeishuBitableFieldInfo,
    ModelConfig,
    RAGQueryBundle,
    RAGQueryVariant,
    RetrievalDebugInfo,
    SupportIssueAgentConfig,
    SupportIssueCaseCandidate,
    SupportIssueClassificationResult,
    SupportIssueDraftResult,
    SupportIssueFeedbackFact,
    SupportIssueFeedbackSnapshot,
    SupportIssueOwnerRule,
    SupportIssueReviewResult,
)
from app.services.support_issue_store import SupportIssueStore


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class FakeRetrievalResult:
    """模拟检索服务返回。"""

    summary: str
    citations: list[str]
    debug: RetrievalDebugInfo | None = None


class FakeSupportIssueStore:
    """只实现 graph 测试需要的最小 Store 能力。"""

    def __init__(self) -> None:
        self.feedback_facts: dict[str, SupportIssueFeedbackFact] = {}
        self.case_candidates: dict[str, SupportIssueCaseCandidate] = {}
        self.feedback_history: list[dict[str, Any]] = []
        self.runs: list[Any] = []
        self.digest_runs: list[Any] = []

    def list_approved_case_candidates(self, _agent_id: str) -> list[SupportIssueCaseCandidate]:
        return [item for item in self.case_candidates.values() if item.status == "approved"]

    def record_run(self, _agent_id: str, run: Any) -> None:
        self.runs.append(run)

    def get_feedback_fact(self, _agent_id: str, record_id: str) -> SupportIssueFeedbackFact | None:
        return self.feedback_facts.get(record_id)

    def upsert_feedback_fact(self, fact: SupportIssueFeedbackFact) -> SupportIssueFeedbackFact:
        self.feedback_facts[fact.record_id] = fact
        return fact

    def append_feedback_history(
        self,
        *,
        agent_id: str,
        record_id: str,
        changed_fields: list[str],
        previous_snapshot: dict[str, object],
        current_snapshot: dict[str, object],
        changed_at: datetime | None = None,
    ) -> None:
        self.feedback_history.append(
            {
                "agent_id": agent_id,
                "record_id": record_id,
                "changed_fields": changed_fields,
                "previous_snapshot": previous_snapshot,
                "current_snapshot": current_snapshot,
                "changed_at": changed_at,
            }
        )

    def get_case_candidate_by_record(self, _agent_id: str, record_id: str) -> SupportIssueCaseCandidate | None:
        return self.case_candidates.get(record_id)

    def upsert_case_candidate(
        self,
        candidate: SupportIssueCaseCandidate,
        *,
        reset_to_pending_review: bool,
    ) -> SupportIssueCaseCandidate:
        stored = candidate
        if reset_to_pending_review:
            stored = candidate.model_copy(
                update={
                    "status": "pending_review",
                    "review_comment": "",
                    "knowledge_document_id": None,
                    "approved_at": None,
                    "approved_by": None,
                }
            )
        self.case_candidates[candidate.record_id] = stored
        return stored

    def list_feedback_facts(self, _agent_id: str) -> list[SupportIssueFeedbackFact]:
        return list(self.feedback_facts.values())

    def list_case_candidates(self, _agent_id: str) -> list[SupportIssueCaseCandidate]:
        return list(self.case_candidates.values())

    def record_digest_run(self, *, agent_id: str, run: Any, items: list[dict[str, object]] | None = None) -> None:
        self.digest_runs.append((agent_id, run, items or []))


class FakeFeishuService:
    """返回预置多维表格记录。"""

    def __init__(self, runtime: "FakeRuntime") -> None:
        self.runtime = runtime

    def list_bitable_records(self, *, app_token: str, table_id: str) -> list[dict[str, Any]]:
        assert app_token == self.runtime.agent.feishu_app_token
        return list(self.runtime.rows_by_table.get(table_id, self.runtime.rows))

    def list_bitable_tables(self, *, app_token: str) -> list[dict[str, Any]]:
        assert app_token == self.runtime.agent.feishu_app_token
        return [
            {
                "table_id": item["table_id"],
                "name": item["table_name"],
            }
            for item in self.runtime.table_contexts
        ]


class FakeRetrievalService:
    """按测试场景返回不同检索结果。"""

    def __init__(self, runtime: "FakeRuntime") -> None:
        self.runtime = runtime

    def run(
        self,
        *,
        query: str,
        scope_type: str,
        scope_id: str | None,
        model_config: ModelConfig,
        system_prompt: str,
        retrieval_profile: str = "default",
        query_bundle_context: dict[str, Any] | None = None,
    ) -> FakeRetrievalResult:
        self.runtime.last_retrieval_context = {
            "query": query,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "model": model_config.model,
            "system_prompt": system_prompt,
            "retrieval_profile": retrieval_profile,
            "query_bundle_context": query_bundle_context or {},
        }
        debug = RetrievalDebugInfo(
            retrieval_profile="support_issue" if retrieval_profile == "support_issue" else "default",
            query_bundle=RAGQueryBundle(
                original_query=query,
                normalized_query=query,
                rewritten_query=query,
                keyword_queries=[],
                sub_queries=[],
                must_terms=[],
                filters={},
                query_variants=[RAGQueryVariant(label="original", query=query, source="original")],
            ),
            candidate_count=0 if self.runtime.retrieval_mode == "no_hit" else 3,
            selected_count=0 if self.runtime.retrieval_mode == "no_hit" else 1,
            selected_chunks=[],
            rerank_preview=[],
        )
        if self.runtime.retrieval_mode == "no_hit":
            return FakeRetrievalResult(summary="", citations=[], debug=debug)
        return FakeRetrievalResult(summary=self.runtime.retrieval_summary, citations=["doc-1"], debug=debug)


class FakeMailService:
    """记录发信动作，并支持按需制造失败。"""

    def __init__(self, runtime: "FakeRuntime") -> None:
        self.runtime = runtime

    def send_email(self, *, recipient_emails: list[str], subject: str, body: str, html_body: str | None = None) -> None:
        if self.runtime.fail_mail:
            raise RuntimeError("mail failed")
        self.runtime.sent_emails.append(
            {
                "recipient_emails": recipient_emails,
                "subject": subject,
                "body": body,
                "html_body": html_body,
            }
        )


class FakeLLMService:
    """模拟支持问题多子 agent 的结构化输出。"""

    def __init__(self, runtime: "FakeRuntime") -> None:
        self.runtime = runtime

    def classify_support_issue(
        self,
        *,
        question: str,
        composed_query: str,
        module_value: str,
        fallback_category: str,
        similar_case_context: str,
        model_config: ModelConfig,
    ) -> SupportIssueClassificationResult:
        return SupportIssueClassificationResult(
            category=fallback_category or "FAQ",
            composed_query=composed_query or question,
            reasoning=f"fake classify:{module_value or 'none'}",
            supervisor_notes="fake supervisor note",
        )

    def draft_support_solution(
        self,
        *,
        question: str,
        category: str,
        retrieval_summary: str,
        retrieval_hit_count: int,
        similar_case_context: str,
        similar_case_count: int,
        model_config: ModelConfig,
    ) -> SupportIssueDraftResult:
        return SupportIssueDraftResult(
            solution=self.runtime.draft_solution_override or retrieval_summary,
            reasoning=f"fake draft:{category}",
            used_similar_case_count=similar_case_count,
        )

    def review_support_solution(
        self,
        *,
        question: str,
        category: str,
        draft_solution: str,
        retrieval_hit_count: int,
        evidence_summary: str,
        fallback_judge_status: str,
        fallback_confidence_score: float,
        fallback_reason: str,
        model_config: ModelConfig,
    ) -> SupportIssueReviewResult:
        status, confidence, reason = self.runtime.review_result_override or (
            fallback_judge_status,
            fallback_confidence_score,
            fallback_reason,
        )
        normalized_status = "pass" if status == "pass" else "manual_review"
        return SupportIssueReviewResult(
            judge_status=normalized_status,
            confidence_score=confidence,
            judge_reason=reason,
            progress_value=DONE_PROGRESS_VALUE if normalized_status == "pass" else MANUAL_REVIEW_PROGRESS_VALUE,
            reviewer_notes="fake review",
        )


class FakeRuntime:
    """支持 graph 测试的最小 runtime。"""

    def __init__(self) -> None:
        now = _utc_now()
        self.agent = SupportIssueAgentConfig(
            id="agent-1",
            name="Support Agent",
            description="",
            enabled=True,
            poll_interval_minutes=30,
            feishu_bitable_url="https://example.com/base/app-token?table=table-id",
            feishu_app_token="app-token",
            feishu_table_id="table-id",
            model_config=ModelConfig(
                mode="learning",
                provider="mock",
                model="mock-model",
                temperature=0.2,
                max_tokens=512,
            ),
            knowledge_scope_type="global",
            knowledge_scope_id=None,
            question_field_name="问题",
            answer_field_name="AI解决方案",
            link_field_name="相关文档链接",
            progress_field_name="回复进度",
            status_field_name="处理状态",
            module_field_name="负责模块",
            registrant_field_name="登记人",
            feedback_result_field_name="人工处理结果",
            feedback_final_answer_field_name="人工最终方案",
            feedback_comment_field_name="反馈备注",
            confidence_field_name="AI置信度",
            hit_count_field_name="命中知识数",
            support_owner_rules=[SupportIssueOwnerRule(module_value="工作台", yht_user_id="owner-1")],
            fallback_support_yht_user_id="fallback-owner",
            digest_enabled=True,
            digest_recipient_emails=["digest@example.com"],
            case_review_enabled=True,
            created_at=now,
            updated_at=now,
        )
        self.rows: list[dict[str, Any]] = []
        self.rows_by_table: dict[str, list[dict[str, Any]]] = {}
        self.table_contexts = [
            {
                "table_id": "table-id",
                "table_name": "开通",
                "bitable_url": "https://example.com/base/app-token?table=table-id",
            }
        ]
        self.retrieval_mode = "success"
        self.retrieval_summary = "这是一个足够详细的处理方案，包含步骤与注意事项。"
        self.judge_result = ("pass", 0.9, "答案匹配度和证据充分性通过。")
        self.review_result_override: tuple[str, float, str] | None = None
        self.draft_solution_override: str | None = None
        self.write_failures_remaining = 0
        self.fail_mail = False
        self.updated_rows: dict[str, list[dict[str, Any]]] = {}
        self.progress_only_updates: list[tuple[str, str]] = []
        self.owner_notifications: list[str] = []
        self.registrant_notifications: list[str] = []
        self.deleted_documents: list[str] = []
        self.sent_emails: list[dict[str, Any]] = []
        self.last_retrieval_context: dict[str, Any] | None = None
        self.support_issue_store = FakeSupportIssueStore()
        self.feishu_service = FakeFeishuService(self)
        self.retrieval_service = FakeRetrievalService(self)
        self.mail_service = FakeMailService(self)
        self.llm_service = FakeLLMService(self)

    def get_agent(self, agent_id: str) -> SupportIssueAgentConfig:
        assert agent_id == self.agent.id
        return self.agent

    def _resolve_runtime_field_mapping(
        self,
        agent: SupportIssueAgentConfig,
        *,
        records: list[dict[str, Any]],
    ) -> dict[str, FeishuBitableFieldInfo]:
        return {
            "question": FeishuBitableFieldInfo(field_name=agent.question_field_name),
            "answer": FeishuBitableFieldInfo(field_name=agent.answer_field_name),
            "link": FeishuBitableFieldInfo(field_name=agent.link_field_name),
            "progress": FeishuBitableFieldInfo(field_name=agent.progress_field_name),
            "module": FeishuBitableFieldInfo(field_name=agent.module_field_name),
            "registrant": FeishuBitableFieldInfo(field_name=agent.registrant_field_name),
            "feedback_result": FeishuBitableFieldInfo(field_name=agent.feedback_result_field_name),
            "feedback_final_answer": FeishuBitableFieldInfo(field_name=agent.feedback_final_answer_field_name),
            "feedback_comment": FeishuBitableFieldInfo(field_name=agent.feedback_comment_field_name),
            "confidence": FeishuBitableFieldInfo(field_name=agent.confidence_field_name),
            "hit_count": FeishuBitableFieldInfo(field_name=agent.hit_count_field_name),
        }

    def _collect_historical_cases(
        self,
        *,
        rows: list[dict[str, Any]],
        question_field_name: str,
        agent: SupportIssueAgentConfig,
    ) -> list[dict[str, str]]:
        return []

    def _row_needs_processing(self, progress_field_name: str, fields: dict[str, Any]) -> bool:
        return self._stringify_field_value(fields.get(progress_field_name)) in {"待分析", "失败待重试"}

    def _normalize_scope(self, scope_type: str, scope_id: str | None) -> tuple[str, str | None]:
        return scope_type, scope_id

    def _build_scoped_record_id(self, *, table_id: str, record_id: str) -> str:
        return f"{table_id}::{record_id}" if table_id and record_id else record_id

    def _row_table_id(self, row: dict[str, Any], *, agent: SupportIssueAgentConfig | None = None) -> str:
        return str(row.get("__agentdemo_table_id") or (agent.feishu_table_id if agent is not None else "")).strip()

    def _row_table_name(self, row: dict[str, Any]) -> str:
        return str(row.get("__agentdemo_table_name") or "").strip()

    def _row_bitable_url(self, row: dict[str, Any], *, agent: SupportIssueAgentConfig) -> str:
        return str(row.get("__agentdemo_bitable_url") or agent.feishu_bitable_url).strip()

    def _list_all_agent_rows(self, agent: SupportIssueAgentConfig) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        rows: list[dict[str, Any]] = []
        for table in self.table_contexts:
            table_rows = self.rows_by_table.get(table["table_id"], self.rows if table["table_id"] == agent.feishu_table_id else [])
            for row in table_rows:
                annotated = dict(row)
                annotated["__agentdemo_table_id"] = table["table_id"]
                annotated["__agentdemo_table_name"] = table["table_name"]
                annotated["__agentdemo_bitable_url"] = table["bitable_url"]
                rows.append(annotated)
        return rows, list(self.table_contexts)

    def _is_url_like_field(self, field: FeishuBitableFieldInfo | None) -> bool:
        return False

    def _extract_record_id(self, row: dict[str, Any]) -> str:
        return str(row.get("record_id") or row.get("recordId") or "").strip()

    def _stringify_field_value(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            return " / ".join(self._stringify_field_value(item) for item in value if self._stringify_field_value(item) != "")
        if isinstance(value, dict):
            for key in ("text", "name", "title", "value", "label", "url"):
                nested = self._stringify_field_value(value.get(key))
                if nested != "":
                    return nested
            return ""
        return str(value).strip()

    def _extract_feedback_snapshot(self, agent: SupportIssueAgentConfig, fields: dict[str, Any]) -> SupportIssueFeedbackSnapshot:
        return SupportIssueFeedbackSnapshot(
            result=self._stringify_field_value(fields.get(agent.feedback_result_field_name)),
            final_solution=self._stringify_field_value(fields.get(agent.feedback_final_answer_field_name)),
            comment=self._stringify_field_value(fields.get(agent.feedback_comment_field_name)),
        )

    def _compose_query(self, *, question: str, fields: dict[str, Any]) -> str:
        return question

    def _classify_question(self, query: str) -> str:
        return "FAQ"

    def _select_similar_cases(self, *, query: str, cases: list[dict[str, str]]) -> list[dict[str, str]]:
        return []

    def _build_similar_case_context(self, similar_cases: list[dict[str, str]]) -> str:
        return ""

    def _update_row_fields(
        self,
        agent: SupportIssueAgentConfig,
        *,
        record_id: str,
        fields: dict[str, Any],
        table_id: str | None = None,
    ) -> None:
        self.updated_rows.setdefault(record_id, []).append(fields)
        if record_id != "" and fields.get(agent.progress_field_name) != PROCESSING_PROGRESS_VALUE and self.write_failures_remaining > 0:
            self.write_failures_remaining -= 1
            raise RuntimeError("write failed")

    def _mark_record_progress_only(
        self,
        agent: SupportIssueAgentConfig,
        *,
        record_id: str,
        progress_field_name: str,
        progress_value: str,
        table_id: str | None = None,
    ) -> None:
        self.progress_only_updates.append((record_id, progress_value))

    def _compose_system_prompt(self, category: str) -> str:
        return f"prompt:{category}"

    def _empty_link_field_value(self, *, url_like_field: bool) -> Any:
        return None if url_like_field else ""

    def _build_runtime_update_fields(self, *field_pairs: tuple[FeishuBitableFieldInfo | None, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for field, value in field_pairs:
            if field is None or field.field_name.strip() == "":
                continue
            payload[field.field_name] = value
        return payload

    def _join_related_document_links(self, retrieval_result: FakeRetrievalResult, *, url_like_field: bool = False) -> str:
        return "" if len(retrieval_result.citations) == 0 else "https://doc.example.com/1"

    def _build_link_field_value(self, retrieval_result: FakeRetrievalResult, *, url_like_field: bool) -> Any:
        return "" if len(retrieval_result.citations) == 0 else "https://doc.example.com/1"

    def _judge_solution(self, *, question: str, summary: str, retrieval_hit_count: int) -> tuple[str, float, str]:
        return self.judge_result

    def _notify_support_owner_for_manual_review(
        self,
        *,
        agent: SupportIssueAgentConfig,
        run_id: str,
        record_id: str,
        source_bitable_url: str,
        source_table_name: str,
        question: str,
        module_value: str,
        solution: str,
    ) -> None:
        self.owner_notifications.append(record_id)

    def _build_feedback_fact(
        self,
        *,
        agent: SupportIssueAgentConfig,
        record_id: str,
        fields: dict[str, Any],
        resolved_fields: dict[str, FeishuBitableFieldInfo],
        synced_at: datetime,
        source_bitable_url: str | None = None,
    ) -> SupportIssueFeedbackFact:
        return SupportIssueFeedbackFact(
            id=f"fact-{record_id}",
            agent_id=agent.id,
            record_id=record_id,
            question=self._stringify_field_value(fields.get(resolved_fields["question"].field_name)),
            progress_value=self._stringify_field_value(fields.get(resolved_fields["progress"].field_name)),
            ai_solution=self._stringify_field_value(fields.get(resolved_fields["answer"].field_name)),
            related_links=[],
            feedback_result=self._stringify_field_value(fields.get(resolved_fields["feedback_result"].field_name)),
            feedback_final_answer=self._stringify_field_value(fields.get(resolved_fields["feedback_final_answer"].field_name)),
            feedback_comment=self._stringify_field_value(fields.get(resolved_fields["feedback_comment"].field_name)),
            confidence_score=float(fields.get(resolved_fields["confidence"].field_name) or 0.0),
            retrieval_hit_count=int(fields.get(resolved_fields["hit_count"].field_name) or 0),
            question_category="FAQ",
            source_bitable_url=source_bitable_url or agent.feishu_bitable_url,
            created_at=synced_at,
            updated_at=synced_at,
            last_synced_at=synced_at,
        )

    def _feedback_fact_snapshot_dict(self, fact: SupportIssueFeedbackFact) -> dict[str, object]:
        return {
            "progress_value": fact.progress_value,
            "feedback_result": fact.feedback_result,
            "feedback_final_answer": fact.feedback_final_answer,
        }

    def _diff_feedback_fact_snapshots(
        self,
        previous_snapshot: dict[str, object],
        current_snapshot: dict[str, object],
    ) -> list[str]:
        return [key for key, value in current_snapshot.items() if previous_snapshot.get(key) != value]

    def _backfill_support_owner_notification_if_needed(
        self,
        *,
        agent: SupportIssueAgentConfig,
        record_id: str,
        fields: dict[str, Any],
        resolved_fields: dict[str, FeishuBitableFieldInfo],
        fact: SupportIssueFeedbackFact,
        synced_at: datetime,
        source_bitable_url: str,
        source_table_name: str,
    ) -> None:
        if fact.progress_value == MANUAL_REVIEW_PROGRESS_VALUE:
            self.owner_notifications.append(record_id)

    def _notify_registrants_for_confirmation_completed(
        self,
        *,
        agent: SupportIssueAgentConfig,
        record_id: str,
        fields: dict[str, Any],
        resolved_fields: dict[str, FeishuBitableFieldInfo],
        fact: SupportIssueFeedbackFact,
        progress_changed_at: datetime,
        source_bitable_url: str,
        source_table_name: str,
    ) -> None:
        self.registrant_notifications.append(record_id)

    def _feedback_fact_to_candidate(self, fact: SupportIssueFeedbackFact) -> SupportIssueCaseCandidate:
        return SupportIssueCaseCandidate(
            id=f"candidate-{fact.record_id}",
            agent_id=fact.agent_id,
            record_id=fact.record_id,
            status="pending_review",
            question=fact.question,
            ai_draft=fact.ai_solution,
            feedback_result=fact.feedback_result,
            final_solution=fact.feedback_final_answer,
            feedback_comment=fact.feedback_comment,
            confidence_score=fact.confidence_score,
            retrieval_hit_count=fact.retrieval_hit_count,
            question_category=fact.question_category,
            related_links=fact.related_links,
            source_bitable_url=fact.source_bitable_url,
            review_comment="",
            knowledge_document_id=None,
            approved_at=None,
            approved_by=None,
            created_at=fact.created_at,
            updated_at=fact.updated_at,
        )

    def _should_create_case_candidate(self, fact: SupportIssueFeedbackFact) -> bool:
        return fact.feedback_result == FEEDBACK_ACCEPTED and fact.feedback_final_answer.strip() != ""

    def _case_candidate_payload_changed(
        self,
        current: SupportIssueCaseCandidate,
        next_candidate: SupportIssueCaseCandidate,
    ) -> bool:
        return current.final_solution != next_candidate.final_solution or current.feedback_comment != next_candidate.feedback_comment

    def _delete_case_candidate_document(self, candidate: SupportIssueCaseCandidate) -> None:
        self.deleted_documents.append(candidate.id)

    def _build_digest_period(self, now: datetime) -> tuple[datetime, datetime]:
        return now - timedelta(days=7), now

    def _question_topic(self, question: str) -> str:
        return question[:80] if question else "未命名问题"

    def _safe_rate(self, numerator: int, denominator: int) -> float:
        if denominator <= 0:
            return 0.0
        return numerator / denominator

    def _build_digest_summary(
        self,
        *,
        total_processed_count: int,
        generated_count: int,
        manual_review_count: int,
        no_hit_count: int,
        failed_count: int,
        acceptance_count: int,
        revised_acceptance_count: int,
        rejected_count: int,
        new_candidate_count: int,
        approved_candidate_count: int,
    ) -> str:
        return f"本周期处理 {total_processed_count} 条问题。"

    def _build_digest_email_bodies(self, *, agent: SupportIssueAgentConfig, digest_run: Any) -> tuple[str, str]:
        return (f"{agent.name}:{digest_run.email_subject}", "<html></html>")


class SupportIssueGraphTests(unittest.TestCase):
    """覆盖核心 graph 节点分支。"""

    def setUp(self) -> None:
        self.runtime = FakeRuntime()
        self.row_graph = SupportIssueRowGraph(self.runtime)
        self.feedback_graph = SupportIssueFeedbackGraph(self.runtime)
        self.digest_graph = SupportIssueDigestGraph(self.runtime, self.feedback_graph)
        self.run_graph = SupportIssueRunGraph(self.runtime, self.row_graph, self.feedback_graph)

    def _scoped_record_id(self, record_id: str, table_id: str = "table-id") -> str:
        return f"{table_id}::{record_id}"

    def _build_row(self, *, record_id: str = "rec-1", question: str = "如何处理登录失败？", progress: str = "待分析") -> dict[str, Any]:
        return {
            "record_id": record_id,
            "fields": {
                "问题": question,
                "回复进度": progress,
                "负责模块": "工作台",
                "登记人": "wangyahui@yonyou.com",
                "人工处理结果": "",
                "人工最终方案": "",
                "反馈备注": "",
                "AI置信度": 0.0,
                "命中知识数": 0,
                "AI解决方案": "",
            },
        }

    def _invoke_row_graph(self, row: dict[str, Any]):
        state = {
            "run_id": "run-1",
            "agent": self.runtime.agent,
            "record": row,
            "resolved_fields": self.runtime._resolve_runtime_field_mapping(self.runtime.agent, records=[row]),
            "historical_cases": [],
            "scope_type": "global",
            "scope_id": None,
            "link_is_url_like": False,
            "graph_trace": [],
        }
        final_state = self.row_graph.graph.invoke(state)
        return final_state["row_result"]

    def test_row_graph_question_empty_marks_failed(self) -> None:
        row_result = self._invoke_row_graph(self._build_row(question=""))
        self.assertEqual(row_result.status, "failed")
        self.assertEqual(self.runtime.updated_rows[self._scoped_record_id("rec-1")][-1]["回复进度"], "失败待重试")
        self.assertTrue(any(item.node == "handle_empty_question" for item in row_result.graph_trace))

    def test_row_graph_no_hit_notifies_support_owner(self) -> None:
        self.runtime.retrieval_mode = "no_hit"
        row_result = self._invoke_row_graph(self._build_row())
        self.assertEqual(row_result.status, "no_hit")
        self.assertIn(self._scoped_record_id("rec-1"), self.runtime.owner_notifications)
        self.assertEqual(self.runtime.updated_rows[self._scoped_record_id("rec-1")][-1]["AI解决方案"], NO_HIT_MESSAGE)
        self.assertTrue(any(item.node == "evidence_agent" for item in row_result.graph_trace))

    def test_row_graph_low_confidence_routes_manual_review(self) -> None:
        self.runtime.judge_result = ("manual_review", 0.4, "答案证据不足。")
        row_result = self._invoke_row_graph(self._build_row())
        self.assertEqual(row_result.status, "manual_review")
        self.assertIn(self._scoped_record_id("rec-1"), self.runtime.owner_notifications)
        self.assertEqual(self.runtime.updated_rows[self._scoped_record_id("rec-1")][-1]["回复进度"], MANUAL_REVIEW_PROGRESS_VALUE)

    def test_row_graph_success_generates_solution(self) -> None:
        row_result = self._invoke_row_graph(self._build_row())
        self.assertEqual(row_result.status, "generated")
        self.assertEqual(self.runtime.updated_rows[self._scoped_record_id("rec-1")][-1]["回复进度"], DONE_PROGRESS_VALUE)
        self.assertEqual(self.runtime.owner_notifications, [])
        self.assertTrue(any(item.node == "classifier_agent" for item in row_result.graph_trace))
        self.assertTrue(any(item.node == "draft_agent" for item in row_result.graph_trace))
        self.assertTrue(any(item.node == "review_agent" for item in row_result.graph_trace))

    def test_row_graph_writeback_failure_turns_failed(self) -> None:
        self.runtime.write_failures_remaining = 1
        row_result = self._invoke_row_graph(self._build_row())
        self.assertEqual(row_result.status, "failed")
        self.assertTrue(any(item.node == "write_row" and item.status == "failed" for item in row_result.graph_trace))

    def test_feedback_graph_confirmation_only_on_transition(self) -> None:
        synced_at = _utc_now() - timedelta(minutes=10)
        previous_fact = SupportIssueFeedbackFact(
            id="fact-rec-1",
            agent_id=self.runtime.agent.id,
            record_id=self._scoped_record_id("rec-1"),
            question="问题1",
            progress_value="AI分析完成",
            ai_solution="旧答案",
            related_links=[],
            feedback_result="",
            feedback_final_answer="",
            feedback_comment="",
            confidence_score=0.5,
            retrieval_hit_count=1,
            question_category="FAQ",
            source_bitable_url=self.runtime.agent.feishu_bitable_url,
            created_at=synced_at,
            updated_at=synced_at,
            last_synced_at=synced_at,
        )
        self.runtime.support_issue_store.feedback_facts[self._scoped_record_id("rec-1")] = previous_fact
        self.runtime.rows = [
            {
                "record_id": "rec-1",
                "fields": {
                    "问题": "问题1",
                    "回复进度": HUMAN_CONFIRMED_PROGRESS_VALUE,
                    "负责模块": "工作台",
                    "登记人": "wangyahui@yonyou.com",
                    "人工处理结果": FEEDBACK_ACCEPTED,
                    "人工最终方案": "人工方案",
                    "反馈备注": "",
                    "AI置信度": 0.8,
                    "命中知识数": 1,
                    "AI解决方案": "旧答案",
                },
            }
        ]
        final_state = self.feedback_graph.graph.invoke(
            {
                "agent_id": self.runtime.agent.id,
                "agent": self.runtime.agent,
                "synced_at": _utc_now(),
                "graph_trace": [],
            }
        )
        response = final_state["response"]
        self.assertEqual(response.fact_upsert_count, 1)
        self.assertEqual(self.runtime.registrant_notifications, [self._scoped_record_id("rec-1")])

        self.runtime.registrant_notifications.clear()
        self.runtime.support_issue_store.feedback_facts[self._scoped_record_id("rec-1")] = self.runtime.support_issue_store.feedback_facts[self._scoped_record_id("rec-1")].model_copy(
            update={"progress_value": HUMAN_CONFIRMED_PROGRESS_VALUE}
        )
        self.feedback_graph.graph.invoke(
            {
                "agent_id": self.runtime.agent.id,
                "agent": self.runtime.agent,
                "synced_at": _utc_now(),
                "graph_trace": [],
            }
        )
        self.assertEqual(self.runtime.registrant_notifications, [])

    def test_run_graph_no_pending_still_triggers_feedback_graph(self) -> None:
        self.runtime.rows = [self._build_row(progress="AI分析完成")]
        final_state = self.run_graph.graph.invoke(
            {
                "agent_id": self.runtime.agent.id,
                "agent": self.runtime.agent,
                "run_id": "run-1",
                "started_at": _utc_now(),
                "graph_trace": [],
                "row_results": [],
            }
        )
        run = final_state["run"]
        self.assertEqual(run.status, "no_change")
        self.assertTrue(any(item.node == "trigger_feedback_graph" for item in run.graph_trace))
        self.assertEqual(len(self.runtime.support_issue_store.runs), 1)

    def test_run_graph_processes_rows_from_all_tables(self) -> None:
        self.runtime.table_contexts = [
            {
                "table_id": "table-id",
                "table_name": "开通",
                "bitable_url": "https://example.com/base/app-token?table=table-id",
            },
            {
                "table_id": "table-2",
                "table_name": "计量",
                "bitable_url": "https://example.com/base/app-token?table=table-2",
            },
        ]
        self.runtime.rows_by_table = {
            "table-id": [self._build_row(record_id="rec-1", question="开通问题")],
            "table-2": [self._build_row(record_id="rec-2", question="计量问题")],
        }
        final_state = self.run_graph.graph.invoke(
            {
                "agent_id": self.runtime.agent.id,
                "agent": self.runtime.agent,
                "run_id": "run-multi",
                "started_at": _utc_now(),
                "graph_trace": [],
                "row_results": [],
            }
        )
        run = final_state["run"]
        self.assertEqual(run.fetched_row_count, 2)
        self.assertEqual(run.processed_row_count, 2)
        self.assertEqual({item.source_table_name for item in run.row_results}, {"开通", "计量"})
        self.assertIn(self._scoped_record_id("rec-1", "table-id"), self.runtime.updated_rows)
        self.assertIn(self._scoped_record_id("rec-2", "table-2"), self.runtime.updated_rows)

    def test_digest_graph_manual_and_scheduled_subjects(self) -> None:
        now = _utc_now()
        self.runtime.support_issue_store.feedback_facts[self._scoped_record_id("rec-1")] = SupportIssueFeedbackFact(
            id="fact-rec-1",
            agent_id=self.runtime.agent.id,
            record_id=self._scoped_record_id("rec-1"),
            question="问题1",
            progress_value=DONE_PROGRESS_VALUE,
            ai_solution="答案1",
            related_links=[],
            feedback_result=FEEDBACK_ACCEPTED,
            feedback_final_answer="人工方案1",
            feedback_comment="",
            confidence_score=0.8,
            retrieval_hit_count=1,
            question_category="FAQ",
            source_bitable_url=self.runtime.agent.feishu_bitable_url,
            created_at=now,
            updated_at=now,
            last_synced_at=now,
        )
        self.runtime.rows = [
            {
                "record_id": "rec-1",
                "fields": {
                    "问题": "问题1",
                    "回复进度": DONE_PROGRESS_VALUE,
                    "负责模块": "工作台",
                    "登记人": "wangyahui@yonyou.com",
                    "人工处理结果": FEEDBACK_ACCEPTED,
                    "人工最终方案": "人工方案1",
                    "反馈备注": "",
                    "AI置信度": 0.8,
                    "命中知识数": 1,
                    "AI解决方案": "答案1",
                },
            }
        ]

        manual_state = self.digest_graph.graph.invoke(
            {
                "agent_id": self.runtime.agent.id,
                "agent": self.runtime.agent,
                "trigger_source": "manual",
                "started_at": now,
                "graph_trace": [],
            }
        )
        scheduled_state = self.digest_graph.graph.invoke(
            {
                "agent_id": self.runtime.agent.id,
                "agent": self.runtime.agent,
                "trigger_source": "scheduled",
                "started_at": now,
                "graph_trace": [],
            }
        )
        self.assertEqual(manual_state["digest_run"].email_subject, "【支持问题 Agent 立即汇总】Support Agent")
        self.assertEqual(scheduled_state["digest_run"].email_subject, "【支持问题 Agent 周期汇总】Support Agent")


class SupportIssueStoreMigrationTests(unittest.TestCase):
    """验证老库迁移后新增 trace 列可用。"""

    def test_store_migration_adds_graph_trace_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            sqlite_path = Path(temp_dir) / "support_issue.sqlite"
            store = SupportIssueStore(sqlite_path)
            self.assertIsNotNone(store)
            with sqlite3.connect(sqlite_path) as conn:
                run_columns = {row[1] for row in conn.execute("PRAGMA table_info(support_issue_runs)").fetchall()}
                digest_columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(support_issue_digest_runs)").fetchall()
                }
            self.assertIn("graph_trace_json", run_columns)
            self.assertIn("graph_trace_json", digest_columns)


if __name__ == "__main__":
    unittest.main()

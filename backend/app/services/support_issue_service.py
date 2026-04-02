"""支持问题 Agent 的业务服务。

这个服务把“读飞书问题表 -> 做 scoped RAG -> 回写答案/状态 -> 同步反馈事实”
组织成一条完整业务链路。

阅读建议：
1. 先看 `run_agent()` 主流程；
2. 再回头理解字段匹配、案例复用、回写格式这些辅助函数；
3. 最后再看 feedback / digest / case candidate 等增强能力。
"""

from __future__ import annotations

from collections import Counter
import difflib
from html import escape
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from ..rag.pipeline import RAGPipeline
from ..schemas import (
    CreateSupportIssueAgentRequest,
    FeishuBitableFieldInfo,
    FeishuBitableFieldsRequest,
    FeishuBitableFieldsResponse,
    FeishuBitablePendingAnalysisRequest,
    FeishuBitablePendingAnalysisResponse,
    FeishuBitablePendingAnalysisRow,
    FeishuBitablePreviewRequest,
    FeishuBitablePreviewResponse,
    FeishuBitableValidationRequest,
    FeishuBitableValidationResponse,
    FeishuBitableWriteValidationRequest,
    FeishuBitableWriteValidationResponse,
    ModelConfig,
    SupportIssueCategoryStat,
    SupportIssueAgentConfig,
    SupportIssueCaseCandidate,
    SupportIssueFeedbackSnapshot,
    SupportIssueFeedbackFact,
    SupportIssueFeedbackSyncResponse,
    SupportIssueDigestRun,
    SupportIssueNotificationEvent,
    SupportIssueOwnerRule,
    SupportIssueInsights,
    SupportIssueRowResult,
    SupportIssueRun,
    UpdateSupportIssueCaseCandidateRequest,
    UpdateSupportIssueAgentRequest,
)
from .feishu_service import FeishuService
from .knowledge_store import ROOT_NODE_ID, KnowledgeStore
from .llm_service import LLMService
from .mail_service import MailService
from .retrieval_service import RetrievalService
from .support_issue_store import SupportIssueStore
from .yonyou_contacts_search_service import YonyouContactsSearchError, YonyouContactsSearchService
from .yonyou_work_notify_service import YonyouWorkNotifyError, YonyouWorkNotifyService


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


DEFAULT_SUPPORT_FIELD_NAMES = ["补充1", "补充2", "补充3", "补充4", "补充5"]
PENDING_ANALYSIS_FIELD_NAME = "回复进度"
PENDING_ANALYSIS_FIELD_VALUE = "待分析"
PENDING_PROGRESS_VALUES = {"待分析", "失败待重试"}
PROCESSING_PROGRESS_VALUE = "分析中"
DONE_PROGRESS_VALUE = "AI分析完成"
MANUAL_REVIEW_PROGRESS_VALUE = "待人工确认"
NO_HIT_PROGRESS_VALUE = "无命中"
FAILED_PROGRESS_VALUE = "失败待重试"
HUMAN_CONFIRMED_PROGRESS_VALUE = "人工确认完成"
NO_HIT_MESSAGE = "未检索到相关知识，已标记待人工确认，请人工补充处理。"
NO_HIT_MESSAGE_KEYWORD = "未检索到相关知识"
LOW_CONFIDENCE_THRESHOLD = 0.65
YONYOU_USER_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
YONYOU_ACCOUNT_PATTERN = re.compile(r"^[A-Za-z0-9._-]{2,64}(?:@yonyou\.com)?$")
REGISTRANT_LOOKUP_FIELD_HINTS = (
    "域名",
    "邮箱",
    "邮件",
    "email",
    "mail",
    "账号",
    "account",
)
MAX_SIMILAR_CASES = 2
FEEDBACK_ACCEPTED = "直接采纳"
FEEDBACK_REVISED_ACCEPTED = "修改后采纳"
FEEDBACK_REJECTED = "驳回"
FEEDBACK_PENDING = "待确认"
QUESTION_CATEGORIES = ("FAQ", "配置排查", "SQL排查", "环境差异", "需升级人工")
SUPPORT_CASE_LIBRARY_ROOT = "支持案例库"
SUPPORT_CASE_LIBRARY_DEFAULT_CATEGORY = "实施常见问题"
SUPPORT_CASE_LIBRARY_CATEGORY_MAP = {
    "SQL排查": "SQL排查",
    "配置排查": "配置排查",
    "环境差异": "环境差异",
    "性能问题": "性能问题",
    "实施常见问题": "实施常见问题",
    "FAQ": "实施常见问题",
    "需升级人工": "实施常见问题",
}
SUPPORT_ISSUE_SYSTEM_PROMPT = (
    "你是支持问题处理助手。"
    "请根据检索命中的知识片段，输出可直接写入问题台账的解决方案。"
    "优先给出排查步骤、处理建议和注意事项；如果信息不足，明确说明限制；"
    "不要编造知识库中不存在的信息；不要输出多余寒暄。"
)


class SupportIssueService:
    """支持问题 Agent 的统一服务入口。"""

    def __init__(
        self,
        support_issue_store: SupportIssueStore,
        knowledge_store: KnowledgeStore,
        llm_service: LLMService,
        feishu_service: FeishuService,
        mail_service: MailService,
        yonyou_work_notify_service: YonyouWorkNotifyService | None = None,
        yonyou_contacts_search_service: YonyouContactsSearchService | None = None,
    ) -> None:
        # SupportIssueService 是支持问题业务的总编排层：
        # - Store 管配置、运行记录、反馈事实、案例候选；
        # - FeishuService 负责和飞书表格交互；
        # - RetrievalService / RAGPipeline 负责 scoped 检索；
        # - LLMService 负责模型能力与结构化判断。
        self.support_issue_store = support_issue_store
        self.knowledge_store = knowledge_store
        self.llm_service = llm_service
        self.feishu_service = feishu_service
        self.mail_service = mail_service
        self.yonyou_work_notify_service = yonyou_work_notify_service or YonyouWorkNotifyService()
        self.yonyou_contacts_search_service = yonyou_contacts_search_service or YonyouContactsSearchService()
        self.rag_pipeline = RAGPipeline(knowledge_store)
        self.retrieval_service = RetrievalService(knowledge_store, llm_service)

    def _require_runnable_model_config(self, model_config: ModelConfig | None) -> ModelConfig:
        try:
            resolved, _provider = self.llm_service.ensure_model_config_runnable(model_config)
            return resolved
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _normalize_scope(self, scope_type: str, scope_id: str | None) -> tuple[str, str | None]:
        # 支持问题 Agent 的“none”会被提升成“global”，
        # 因为它的默认心智更接近“全局知识库”，而不是完全禁用检索。
        if scope_type == "tree_recursive":
            return scope_type, scope_id or ROOT_NODE_ID
        if scope_type == "none":
            return "global", None
        return scope_type, None

    def _stringify_field_value(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float, bool)):
            return str(value).strip()
        if isinstance(value, list):
            parts = [self._stringify_field_value(item) for item in value]
            return " / ".join(part for part in parts if part != "").strip()
        if isinstance(value, dict):
            for key in ("text", "name", "title", "value", "label", "url"):
                nested = value.get(key)
                text = self._stringify_field_value(nested)
                if text != "":
                    return text
            return ""
        return str(value).strip()

    def _normalize_field_match_key(self, value: str) -> str:
        return re.sub(r"[\s\-_—–·`~!@#$%^&*()（）【】\[\]{}<>《》/\\|,，。；;：:、]+", "", value).lower()

    def _field_name_matches(self, field_name: str, candidate: str) -> bool:
        normalized_field = self._normalize_field_match_key(field_name)
        normalized_candidate = self._normalize_field_match_key(candidate)
        if normalized_field == "" or normalized_candidate == "":
            return False
        return (
            normalized_field == normalized_candidate
            or normalized_candidate in normalized_field
            or normalized_field in normalized_candidate
        )

    def _row_needs_processing(self, progress_field_name: str, fields: dict[str, Any]) -> bool:
        progress_text = self._stringify_field_value(fields.get(progress_field_name))
        return progress_text in PENDING_PROGRESS_VALUES

    def _compose_query(self, *, question: str, fields: dict[str, Any]) -> str:
        # 飞书表里经常不止“问题”一列，还有补充说明、现象、备注等辅助信息。
        # 这里把这些字段拼成一条更适合检索和生成的完整 query。
        supplements: list[str] = []
        for field_name in DEFAULT_SUPPORT_FIELD_NAMES:
            text = self._stringify_field_value(fields.get(field_name))
            if text != "":
                supplements.append(f"{field_name}：{text}")

        remark_candidates = ["备注", "现象", "复现步骤", "背景", "说明", "排查记录"]
        for field_name in remark_candidates:
            text = self._stringify_field_value(fields.get(field_name))
            if text != "":
                supplements.append(f"{field_name}：{text}")

        if len(supplements) == 0:
            return question
        return "问题：\n" + question + "\n\n补充信息：\n" + "\n".join(f"- {item}" for item in supplements)

    def _classify_question(self, query: str) -> str:
        # 这是一个轻量规则分类器：
        # 先按关键词把问题归到 SQL / 配置 / 环境 / FAQ 等大类，
        # 再据此选择更贴近场景的 system prompt。
        lowered = query.lower()
        if any(token in lowered for token in ("select ", "update ", "insert ", "delete ", "sql", "数据库", "表 ")):
            return "SQL排查"
        if any(token in lowered for token in ("环境", "租户", "版本", "升级", "发布", "节点", "开通")):
            return "环境差异"
        if any(token in lowered for token in ("配置", "开关", "参数", "权限", "菜单", "流程")):
            return "配置排查"
        if any(token in lowered for token in ("无法", "异常", "报错", "错误", "失败", "超时", "性能", "内存")):
            return "需升级人工"
        return "FAQ"

    def _compose_system_prompt(self, category: str) -> str:
        if category == "SQL排查":
            suffix = "优先输出可执行的 SQL 排查步骤，并标注每一步的预期结果。"
        elif category == "配置排查":
            suffix = "优先输出配置路径、开关项、权限点和验证顺序。"
        elif category == "环境差异":
            suffix = "优先对比环境与版本差异，给出可复现和回归检查项。"
        elif category == "需升级人工":
            suffix = "先给出可做的初步排查，再明确需要人工接管的边界。"
        else:
            suffix = "优先用条目化步骤输出，避免泛泛而谈。"
        return SUPPORT_ISSUE_SYSTEM_PROMPT + suffix

    def _extract_feedback_snapshot(self, agent: SupportIssueAgentConfig, fields: dict[str, Any]) -> SupportIssueFeedbackSnapshot:
        return SupportIssueFeedbackSnapshot(
            result=self._stringify_field_value(fields.get(agent.feedback_result_field_name)),
            final_solution=self._stringify_field_value(fields.get(agent.feedback_final_answer_field_name)),
            comment=self._stringify_field_value(fields.get(agent.feedback_comment_field_name)),
        )

    def _normalize_module_match_key(self, value: str) -> str:
        return self._normalize_field_match_key(value)

    def _resolve_support_owner_user_id(
        self,
        *,
        module_value: str,
        rules: list[SupportIssueOwnerRule],
        fallback_user_id: str,
    ) -> str:
        normalized_module = self._normalize_module_match_key(module_value)
        if normalized_module != "":
            for rule in rules:
                if self._normalize_module_match_key(rule.module_value) == normalized_module:
                    return rule.yht_user_id.strip()
        return fallback_user_id.strip()

    def _extract_user_ids_from_field_value(self, value: Any) -> list[str]:
        user_ids: list[str] = []

        def append_candidate(raw_candidate: Any) -> None:
            candidate = str(raw_candidate or "").strip()
            if candidate == "":
                return
            lowered = candidate.lower()
            if lowered.startswith("ou_") or lowered.startswith("on_") or YONYOU_USER_ID_PATTERN.fullmatch(candidate):
                user_ids.append(candidate)

        def collect(item: Any) -> None:
            if item is None:
                return
            if isinstance(item, str):
                append_candidate(item)
                return
            if isinstance(item, list):
                for nested in item:
                    collect(nested)
                return
            if isinstance(item, dict):
                for key in ("user_id", "userId", "id", "open_id", "openId", "yhtUserId"):
                    append_candidate(item.get(key))
                for key in ("user", "member", "owner"):
                    nested = item.get(key)
                    if nested is not None:
                        collect(nested)
                return
            append_candidate(self._stringify_field_value(item))

        collect(value)
        deduped: list[str] = []
        seen: set[str] = set()
        for item in user_ids:
            normalized = item.strip()
            if normalized == "" or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

    def _extract_contact_lookup_candidates_from_field_value(self, value: Any) -> list[str]:
        candidates: list[str] = []

        def append_candidate(raw_candidate: Any) -> None:
            candidate = str(raw_candidate or "").strip()
            if candidate == "":
                return
            lowered = candidate.lower()
            if lowered.startswith("ou_") or lowered.startswith("on_") or YONYOU_USER_ID_PATTERN.fullmatch(candidate):
                return
            if "@" in candidate:
                if " " in candidate:
                    return
            elif YONYOU_ACCOUNT_PATTERN.fullmatch(candidate) is None:
                return
            if lowered not in {item.lower() for item in candidates}:
                candidates.append(candidate)

        def collect(item: Any) -> None:
            if item is None:
                return
            if isinstance(item, str):
                append_candidate(item)
                return
            if isinstance(item, list):
                for nested in item:
                    collect(nested)
                return
            if isinstance(item, dict):
                for key in ("email", "mail", "work_email", "workEmail", "user_email", "userEmail"):
                    append_candidate(item.get(key))
                for key in ("user", "member", "owner"):
                    nested = item.get(key)
                    if nested is not None:
                        collect(nested)
                return
            append_candidate(self._stringify_field_value(item))

        collect(value)
        return candidates

    def _collect_registrant_lookup_field_names(
        self,
        *,
        fields: dict[str, Any],
        registrant_field_name: str,
    ) -> list[str]:
        candidate_field_names = [
            registrant_field_name,
            "域名（xxx@yonyou.com）",
            "域名",
            "邮箱",
            "邮件",
            "短账号",
            "账号",
            "登记人域名",
            "登记人邮箱",
            "提交人域名",
            "提交人邮箱",
            "提问人域名",
            "提问人邮箱",
        ]
        candidate_field_names.extend(str(field_name) for field_name in fields.keys())

        deduped: list[str] = []
        seen: set[str] = set()
        for field_name in candidate_field_names:
            normalized_field_name = str(field_name or "").strip()
            if normalized_field_name == "":
                continue
            normalized_match_key = self._normalize_field_match_key(normalized_field_name)
            if normalized_match_key == "":
                continue
            if normalized_field_name != registrant_field_name and not any(
                hint in normalized_match_key for hint in REGISTRANT_LOOKUP_FIELD_HINTS
            ):
                continue
            if normalized_match_key in seen:
                continue
            seen.add(normalized_match_key)
            deduped.append(normalized_field_name)
        return deduped

    def _extract_registrant_user_ids(
        self,
        *,
        fields: dict[str, Any],
        registrant_field: FeishuBitableFieldInfo,
    ) -> list[str]:
        resolved_user_ids: list[str] = []
        seen_user_ids: set[str] = set()
        seen_lookup_candidates: set[str] = set()
        field_names = self._collect_registrant_lookup_field_names(
            fields=fields,
            registrant_field_name=registrant_field.field_name,
        )
        for field_name in field_names:
            for user_id in self._extract_user_ids_from_field_value(fields.get(field_name)):
                normalized_user_id = user_id.strip()
                if normalized_user_id == "" or normalized_user_id in seen_user_ids:
                    continue
                seen_user_ids.add(normalized_user_id)
                resolved_user_ids.append(normalized_user_id)
            for candidate in self._extract_contact_lookup_candidates_from_field_value(fields.get(field_name)):
                normalized_candidate = candidate.strip().lower()
                if normalized_candidate == "" or normalized_candidate in seen_lookup_candidates:
                    continue
                seen_lookup_candidates.add(normalized_candidate)
                for user_id in self.yonyou_contacts_search_service.resolve_yht_user_ids(candidate):
                    normalized_user_id = user_id.strip()
                    if normalized_user_id == "" or normalized_user_id in seen_user_ids:
                        continue
                    seen_user_ids.add(normalized_user_id)
                    resolved_user_ids.append(normalized_user_id)
        return resolved_user_ids

    def _registrant_notification_reminder(self) -> str:
        return "如确认问题已闭环，请将“回复进度”改为“完成”，否则后续轮巡仍可能继续提醒。"

    def _record_notification_event(
        self,
        *,
        agent_id: str,
        record_id: str,
        event_type: str,
        recipient_user_id: str,
        status: str,
        error_message: str | None = None,
    ) -> None:
        self.support_issue_store.record_notification_event(
            SupportIssueNotificationEvent(
                id=str(uuid4()),
                agent_id=agent_id,
                record_id=record_id,
                event_type=event_type,
                recipient_user_id=recipient_user_id,
                status=status,
                error_message=error_message,
                created_at=_utc_now(),
            )
        )

    def _has_notification_event(
        self,
        *,
        agent_id: str,
        record_id: str,
        event_type: str,
        statuses: tuple[str, ...] | None = None,
    ) -> bool:
        return self.support_issue_store.has_notification_event(
            agent_id=agent_id,
            record_id=record_id,
            event_type=event_type,
            statuses=statuses,
        )

    def _has_notification_event_for_recipient(
        self,
        *,
        agent_id: str,
        record_id: str,
        event_type: str,
        recipient_user_id: str,
        statuses: tuple[str, ...] | None = None,
    ) -> bool:
        return self.support_issue_store.has_notification_event_for_recipient(
            agent_id=agent_id,
            record_id=record_id,
            event_type=event_type,
            recipient_user_id=recipient_user_id,
            statuses=statuses,
        )

    def _send_yonyou_notification(
        self,
        *,
        agent_id: str,
        record_id: str,
        event_type: str,
        recipient_user_ids: list[str],
        src_msg_id: str,
        title: str,
        content: str,
        web_url: str | None = None,
    ) -> None:
        normalized_recipients = [item.strip() for item in recipient_user_ids if item.strip() != ""]
        if len(normalized_recipients) == 0:
            self._record_notification_event(
                agent_id=agent_id,
                record_id=record_id,
                event_type=event_type,
                recipient_user_id="",
                status="skipped",
                error_message="缺少可用的通知接收人 userId。",
            )
            return

        try:
            response = self.yonyou_work_notify_service.send_work_notify(
                openapi_base_url=None,
                src_msg_id=src_msg_id,
                yht_user_ids=normalized_recipients,
                title=title,
                content=content,
                label_code="OA",
                web_url=web_url,
            )
            if response.get("ok") is not True:
                error_message = str(response.get("message") or "工作通知接口返回非成功状态。").strip()
                code = str(response.get("code") or "").strip()
                if code != "":
                    error_message = f"code={code}: {error_message}"
                raise YonyouWorkNotifyError(error_message)
            for recipient in normalized_recipients:
                self._record_notification_event(
                    agent_id=agent_id,
                    record_id=record_id,
                    event_type=event_type,
                    recipient_user_id=recipient,
                    status="sent",
                )
        except YonyouWorkNotifyError as exc:
            error_message = str(exc)
            for recipient in normalized_recipients:
                self._record_notification_event(
                    agent_id=agent_id,
                    record_id=record_id,
                    event_type=event_type,
                    recipient_user_id=recipient,
                    status="failed",
                    error_message=error_message,
                )

    def _short_text(self, value: str, *, limit: int = 120) -> str:
        compact = " ".join(value.strip().split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1] + "…"

    def _normalize_similarity_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().lower()

    def _case_similarity(self, query: str, candidate: str) -> float:
        normalized_query = self._normalize_similarity_text(query)
        normalized_candidate = self._normalize_similarity_text(candidate)
        if normalized_query == "" or normalized_candidate == "":
            return 0.0
        sequence_ratio = difflib.SequenceMatcher(a=normalized_query, b=normalized_candidate).ratio()
        query_tokens = set(re.findall(r"[a-zA-Z0-9_\u4e00-\u9fff]+", normalized_query))
        candidate_tokens = set(re.findall(r"[a-zA-Z0-9_\u4e00-\u9fff]+", normalized_candidate))
        overlap_ratio = 0.0
        if len(query_tokens) > 0:
            overlap_ratio = len(query_tokens & candidate_tokens) / len(query_tokens)
        return max(sequence_ratio, overlap_ratio)

    def _collect_historical_cases(
        self,
        *,
        rows: list[dict[str, Any]],
        question_field_name: str,
        agent: SupportIssueAgentConfig,
    ) -> list[dict[str, str]]:
        cases: list[dict[str, str]] = []
        for row in rows:
            fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
            question = self._stringify_field_value(fields.get(question_field_name))
            feedback = self._extract_feedback_snapshot(agent, fields)
            if question == "":
                continue
            if feedback.result not in {FEEDBACK_ACCEPTED, FEEDBACK_REVISED_ACCEPTED}:
                continue
            final_solution = feedback.final_solution.strip()
            if final_solution == "":
                continue
            cases.append(
                {
                    "question": question,
                    "solution": final_solution,
                    "feedback_result": feedback.result,
                }
            )
        return cases

    def _select_similar_cases(self, *, query: str, cases: list[dict[str, str]]) -> list[dict[str, str]]:
        scored: list[tuple[float, dict[str, str]]] = []
        for case in cases:
            similarity = self._case_similarity(query, case.get("question", ""))
            if similarity < 0.35:
                continue
            scored.append((similarity, case))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in scored[:MAX_SIMILAR_CASES]]

    def _build_similar_case_context(self, similar_cases: list[dict[str, str]]) -> str:
        if len(similar_cases) == 0:
            return ""
        lines: list[str] = ["历史已采纳案例参考："]
        for index, case in enumerate(similar_cases, start=1):
            lines.append(f"{index}. 问题：{case.get('question', '')}")
            lines.append(f"   最终方案：{case.get('solution', '')}")
        return "\n".join(lines).strip()

    def _judge_solution(
        self,
        *,
        question: str,
        summary: str,
        retrieval_hit_count: int,
    ) -> tuple[str, float, str]:
        if retrieval_hit_count <= 0:
            return "manual_review", 0.0, "未命中知识依据。"

        normalized_summary = summary.strip()
        if normalized_summary == "":
            return "manual_review", 0.15, "生成结果为空。"

        confidence = 0.45
        confidence += min(0.25, retrieval_hit_count * 0.05)
        if len(normalized_summary) >= 120:
            confidence += 0.2
        elif len(normalized_summary) >= 60:
            confidence += 0.1

        risk_keywords = ("可能", "猜测", "无法确认", "不确定", "建议联系", "信息不足", "未找到")
        if any(keyword in normalized_summary for keyword in risk_keywords):
            confidence -= 0.15

        normalized_question_tokens = set(re.findall(r"[a-zA-Z0-9_\u4e00-\u9fff]+", question.lower()))
        normalized_answer_tokens = set(re.findall(r"[a-zA-Z0-9_\u4e00-\u9fff]+", normalized_summary.lower()))
        if len(normalized_question_tokens) > 0:
            overlap_ratio = len(normalized_question_tokens & normalized_answer_tokens) / len(normalized_question_tokens)
            confidence += min(0.15, overlap_ratio * 0.2)
            if overlap_ratio < 0.08:
                confidence -= 0.12

        bounded_confidence = max(0.0, min(1.0, confidence))
        if bounded_confidence < LOW_CONFIDENCE_THRESHOLD:
            return "manual_review", bounded_confidence, "答案证据或匹配度不足，已转人工确认。"
        return "pass", bounded_confidence, "答案匹配度和证据充分性通过。"

    def _best_related_link(self, document_ids: list[str]) -> str | None:
        external_urls = self.knowledge_store.get_document_external_urls(document_ids)
        for document_id in document_ids:
            external_url = external_urls.get(document_id)
            if external_url:
                return external_url
        return None

    def _collect_related_document_links(self, retrieval_result: Any) -> list[dict[str, str]]:
        related_links = getattr(retrieval_result, "related_document_links", []) or []
        items: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        for item in related_links:
            external_url = str(getattr(item, "external_url", "") or "").strip()
            if external_url == "" or external_url in seen_urls:
                continue
            seen_urls.add(external_url)
            items.append(
                {
                    "url": external_url,
                    "document_name": str(getattr(item, "document_name", "") or "").strip(),
                }
            )
        return items

    def _join_related_document_links(self, retrieval_result: Any, *, url_like_field: bool = False) -> str:
        items = self._collect_related_document_links(retrieval_result)
        urls = [item["url"] for item in items]
        if len(urls) == 0:
            return ""
        if url_like_field:
            return urls[0]
        return "\n".join(urls)

    def _empty_link_field_value(self, *, url_like_field: bool) -> Any:
        return None if url_like_field else ""

    def _build_link_field_value(self, retrieval_result: Any, *, url_like_field: bool) -> Any:
        items = self._collect_related_document_links(retrieval_result)
        if len(items) == 0:
            return self._empty_link_field_value(url_like_field=url_like_field)
        if not url_like_field:
            return "\n".join(item["url"] for item in items)
        first_item = items[0]
        return {
            "link": first_item["url"],
            "text": first_item["document_name"] or first_item["url"],
        }

    def _is_no_hit_feedback_fact(self, fact: SupportIssueFeedbackFact) -> bool:
        ai_solution = fact.ai_solution.strip()
        return fact.retrieval_hit_count == 0 and NO_HIT_MESSAGE_KEYWORD in ai_solution

    def _coerce_field_value_for_write(self, field: FeishuBitableFieldInfo | None, value: Any) -> Any:
        if field is None:
            return value
        if value is None:
            return None
        if self._is_url_like_field(field):
            return value
        if self._is_text_like_field(field):
            if isinstance(value, str):
                return value
            return self._stringify_field_value(value)
        if field.ui_type in {"SingleSelect"} or field.type in {3}:
            return self._stringify_field_value(value)
        return value

    def _build_update_fields(self, *field_pairs: tuple[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for field_name, value in field_pairs:
            normalized_name = field_name.strip()
            if normalized_name == "":
                continue
            payload[normalized_name] = value
        return payload

    def _build_runtime_update_fields(self, *field_pairs: tuple[FeishuBitableFieldInfo | None, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for field, value in field_pairs:
            if field is None:
                continue
            field_name = field.field_name.strip()
            if field_name == "":
                continue
            payload[field_name] = self._coerce_field_value_for_write(field, value)
        return payload

    def _build_bitable_context(self, raw_url: str) -> dict[str, str | None]:
        return self.feishu_service.parse_bitable_url(raw_url)

    def _build_bitable_base_response(self, *, ok: bool, message: str, parsed: dict[str, str | None]) -> dict[str, Any]:
        return {
            "ok": ok,
            "message": message,
            "normalized_url": str(parsed.get("normalized_url") or ""),
            "parsed_app_token": str(parsed.get("app_token") or ""),
            "parsed_table_id": str(parsed.get("table_id") or ""),
            "parsed_view_id": parsed.get("view_id"),
        }

    def _extract_record_fields(self, record: dict[str, Any] | None, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
        if isinstance(record, dict):
            fields = record.get("fields")
            if isinstance(fields, dict):
                return fields
        return dict(fallback or {})

    def _friendly_write_validation_message(self, raw_message: str) -> str:
        normalized = raw_message.strip()
        lowered = normalized.lower()
        if "91403" in normalized or "403" in normalized or "forbidden" in lowered:
            return (
                "当前飞书应用已经具备读表能力，但没有表格写入权限。"
                "请在飞书开放平台为该自建应用补充多维表格记录写权限，并发布版本；"
                "同时确认目标多维表格允许该应用写入。"
            )
        if "字段" in normalized or "field" in lowered:
            return f"编辑验证失败：字段映射可能不匹配。原始错误：{normalized}"
        return f"编辑验证失败：{normalized}"

    def _normalize_field_info(self, raw_field: dict[str, Any]) -> FeishuBitableFieldInfo | None:
        field_name = str(
            raw_field.get("field_name")
            or raw_field.get("fieldName")
            or raw_field.get("name")
            or raw_field.get("title")
            or ""
        ).strip()
        if field_name == "":
            return None
        field_property = raw_field.get("property") if isinstance(raw_field.get("property"), dict) else {}
        raw_type = raw_field.get("type")
        normalized_type = raw_type if isinstance(raw_type, int) else None
        ui_type = str(raw_field.get("ui_type") or raw_field.get("uiType") or "").strip() or None
        is_primary = bool(raw_field.get("is_primary") or raw_field.get("isPrimary") or field_property.get("primary"))
        field_id = str(raw_field.get("field_id") or raw_field.get("fieldId") or "").strip() or None
        return FeishuBitableFieldInfo(
            field_id=field_id,
            field_name=field_name,
            type=normalized_type,
            ui_type=ui_type,
            is_primary=is_primary,
            property=field_property,
        )

    def _is_text_like_field(self, field: FeishuBitableFieldInfo) -> bool:
        if field.ui_type in {"Text", "Barcode"}:
            return True
        return field.type in {1}

    def _pick_field_by_candidates(
        self,
        fields: list[FeishuBitableFieldInfo],
        candidates: list[str],
        *,
        exclude_names: set[str] | None = None,
    ) -> FeishuBitableFieldInfo | None:
        excluded = {name.strip() for name in (exclude_names or set()) if name.strip() != ""}
        normalized_excluded = {self._normalize_field_match_key(name) for name in excluded if name != ""}
        lookup = {field.field_name.strip(): field for field in fields if field.field_name.strip() != ""}
        for candidate in candidates:
            normalized = candidate.strip()
            if normalized == "" or normalized in excluded:
                continue
            matched = lookup.get(normalized)
            if matched is not None:
                return matched
        for candidate in candidates:
            normalized = candidate.strip()
            normalized_key = self._normalize_field_match_key(normalized)
            if normalized_key == "" or normalized_key in normalized_excluded:
                continue
            for field in fields:
                field_key = self._normalize_field_match_key(field.field_name)
                if field_key == "" or field_key in normalized_excluded:
                    continue
                if field_key == normalized_key:
                    return field
        for candidate in candidates:
            normalized = candidate.strip()
            normalized_key = self._normalize_field_match_key(normalized)
            if normalized_key == "" or normalized_key in normalized_excluded:
                continue
            for field in fields:
                field_key = self._normalize_field_match_key(field.field_name)
                if field_key == "" or field_key in normalized_excluded:
                    continue
                if self._field_name_matches(field.field_name, normalized):
                    return field
        return None

    def _pick_first_writable_text_field(
        self,
        fields: list[FeishuBitableFieldInfo],
        *,
        exclude_names: set[str] | None = None,
    ) -> FeishuBitableFieldInfo | None:
        excluded = {name.strip() for name in (exclude_names or set()) if name.strip() != ""}
        primary_text_field = next(
            (
                field
                for field in fields
                if field.field_name not in excluded and field.is_primary and self._is_text_like_field(field)
            ),
            None,
        )
        if primary_text_field is not None:
            return primary_text_field
        return next(
            (
                field
                for field in fields
                if field.field_name not in excluded and self._is_text_like_field(field)
            ),
            None,
        )

    def _resolve_write_validation_fields(
        self,
        *,
        fields: list[FeishuBitableFieldInfo],
        question_field_name: str,
        answer_field_name: str,
        status_field_name: str,
    ) -> tuple[FeishuBitableFieldInfo, FeishuBitableFieldInfo]:
        create_candidates = [
            question_field_name,
            "问题",
            "标题",
            "主题",
        ]
        create_field = self._pick_field_by_candidates(fields, create_candidates)
        if create_field is None or not self._is_text_like_field(create_field):
            create_field = self._pick_first_writable_text_field(fields)
        if create_field is None:
            raise RuntimeError("当前表中没有可用于创建测试记录的文本字段。")

        excluded = {create_field.field_name}
        update_candidates = [
            answer_field_name,
            "解决方案",
            "AI解决方案",
            "处理状态",
            status_field_name,
            "是否完成",
        ]
        update_field = self._pick_field_by_candidates(fields, update_candidates, exclude_names=excluded)
        if update_field is None or not self._is_text_like_field(update_field):
            update_field = self._pick_first_writable_text_field(fields, exclude_names=excluded)
        if update_field is None:
            update_field = create_field
        return create_field, update_field

    def _fallback_fields_from_preview(self, *, app_token: str, table_id: str) -> list[FeishuBitableFieldInfo]:
        page = self.feishu_service.list_bitable_records_page(
            app_token=app_token,
            table_id=table_id,
            page_size=20,
        )
        field_names: set[str] = set()
        for item in page["items"]:
            fields = item.get("fields")
            if not isinstance(fields, dict):
                continue
            for field_name in fields.keys():
                normalized_name = str(field_name).strip()
                if normalized_name != "":
                    field_names.add(normalized_name)
        return [FeishuBitableFieldInfo(field_name=name) for name in sorted(field_names)]

    def _fallback_fields_from_records(self, records: list[dict[str, Any]]) -> list[FeishuBitableFieldInfo]:
        field_names: set[str] = set()
        for item in records:
            fields = item.get("fields")
            if not isinstance(fields, dict):
                continue
            for field_name in fields.keys():
                normalized_name = str(field_name).strip()
                if normalized_name != "":
                    field_names.add(normalized_name)
        return [FeishuBitableFieldInfo(field_name=name) for name in sorted(field_names)]

    def _load_runtime_field_infos(
        self,
        *,
        app_token: str,
        table_id: str,
        records: list[dict[str, Any]],
    ) -> list[FeishuBitableFieldInfo]:
        try:
            raw_fields = self.feishu_service.list_bitable_fields(app_token=app_token, table_id=table_id)
            normalized_fields = [field for item in raw_fields if (field := self._normalize_field_info(item)) is not None]
            if len(normalized_fields) > 0:
                return normalized_fields
        except Exception:
            pass

        fallback_fields = self._fallback_fields_from_records(records)
        if len(fallback_fields) > 0:
            return fallback_fields
        return self._fallback_fields_from_preview(app_token=app_token, table_id=table_id)

    def _resolve_runtime_field_mapping(
        self,
        agent: SupportIssueAgentConfig,
        *,
        records: list[dict[str, Any]],
    ) -> dict[str, FeishuBitableFieldInfo]:
        available_fields = self._load_runtime_field_infos(
            app_token=agent.feishu_app_token,
            table_id=agent.feishu_table_id,
            records=records,
        )

        question_field = self._pick_field_by_candidates(
            available_fields,
            [agent.question_field_name, "问题", "标题", "主题", "问题描述", "内容"],
        )
        answer_field = self._pick_field_by_candidates(
            available_fields,
            [agent.answer_field_name, "AI解决方案", "解决方案", "处理建议", "回复内容"],
        )
        link_field = self._pick_field_by_candidates(
            available_fields,
            [agent.link_field_name, "相关文档链接", "相关链接", "文档链接", "参考链接", "相关资料链接"],
        )
        progress_field = self._pick_field_by_candidates(
            available_fields,
            [agent.progress_field_name, "回复进度", "处理状态", "进度", "状态"],
        )
        module_field = self._pick_field_by_candidates(
            available_fields,
            [agent.module_field_name, "负责模块", "模块", "业务模块"],
        )
        registrant_field = self._pick_field_by_candidates(
            available_fields,
            [agent.registrant_field_name, "登记人", "提交人", "提问人"],
        )
        feedback_result_field = self._pick_field_by_candidates(
            available_fields,
            [agent.feedback_result_field_name, "人工处理结果", "处理结果", "人工结果"],
        )
        feedback_final_answer_field = self._pick_field_by_candidates(
            available_fields,
            [agent.feedback_final_answer_field_name, "人工最终方案", "最终方案", "人工方案"],
        )
        feedback_comment_field = self._pick_field_by_candidates(
            available_fields,
            [agent.feedback_comment_field_name, "反馈备注", "备注", "反馈说明"],
        )
        confidence_field = self._pick_field_by_candidates(
            available_fields,
            [agent.confidence_field_name, "AI置信度", "置信度"],
        )
        hit_count_field = self._pick_field_by_candidates(
            available_fields,
            [agent.hit_count_field_name, "命中知识数", "命中数", "知识命中数"],
        )

        return {
            "question": question_field or FeishuBitableFieldInfo(field_name=agent.question_field_name),
            "answer": answer_field or FeishuBitableFieldInfo(field_name=agent.answer_field_name),
            "link": link_field or FeishuBitableFieldInfo(field_name=agent.link_field_name),
            "progress": progress_field or FeishuBitableFieldInfo(field_name=agent.progress_field_name),
            "module": module_field or FeishuBitableFieldInfo(field_name=agent.module_field_name),
            "registrant": registrant_field or FeishuBitableFieldInfo(field_name=agent.registrant_field_name),
            "feedback_result": feedback_result_field
            or FeishuBitableFieldInfo(field_name=agent.feedback_result_field_name),
            "feedback_final_answer": feedback_final_answer_field
            or FeishuBitableFieldInfo(field_name=agent.feedback_final_answer_field_name),
            "feedback_comment": feedback_comment_field
            or FeishuBitableFieldInfo(field_name=agent.feedback_comment_field_name),
            "confidence": confidence_field or FeishuBitableFieldInfo(field_name=""),
            "hit_count": hit_count_field or FeishuBitableFieldInfo(field_name=""),
        }

    def _is_url_like_field(self, field: FeishuBitableFieldInfo | None) -> bool:
        if field is None:
            return False
        ui_type = (field.ui_type or "").strip().lower()
        if ui_type in {"url", "link", "hyperlink"}:
            return True
        property_payload = field.property if isinstance(field.property, dict) else {}
        return "url" in str(property_payload).lower() or "link" in str(property_payload).lower()

    def _mark_record_progress_only(
        self,
        agent: SupportIssueAgentConfig,
        *,
        record_id: str,
        progress_field_name: str,
        progress_value: str,
    ) -> None:
        self._update_row_fields(
            agent,
            record_id=record_id,
            fields={progress_field_name: progress_value},
        )

    def _find_content_field_name(self, records: list[dict[str, Any]], fields: list[FeishuBitableFieldInfo]) -> str | None:
        preferred_names = ["问题", "标题", "主题", "内容", "问题描述"]
        existing_names = {field.field_name for field in fields}
        for preferred_name in preferred_names:
            if preferred_name in existing_names:
                return preferred_name
        for record in records:
            raw_fields = record.get("fields")
            if not isinstance(raw_fields, dict):
                continue
            for preferred_name in preferred_names:
                if self._stringify_field_value(raw_fields.get(preferred_name)) != "":
                    return preferred_name
        return None

    def _extract_record_id(self, row: dict[str, Any]) -> str:
        """统一提取飞书记录 ID。

        飞书返回里有时用 `record_id`，有时也可能是 `recordId`。
        这里做统一收口，避免各条链路各自猜字段名。
        """

        return str(row.get("record_id") or row.get("recordId") or "").strip()

    def _extract_urls_from_field_value(self, value: Any) -> list[str]:
        """从飞书字段值里尽量还原 URL 列表。

        这样设计是为了兼容三种常见写法：
        - 普通文本列，按换行写 URL；
        - URL/超链接列，值可能是 `{link, text}`；
        - 多值结构，值可能是 list[dict|str]。
        """

        urls: list[str] = []

        def collect(item: Any) -> None:
            if item is None:
                return
            if isinstance(item, str):
                for line in item.splitlines():
                    normalized = line.strip()
                    if normalized.startswith(("http://", "https://")):
                        urls.append(normalized)
                return
            if isinstance(item, dict):
                for key in ("link", "url", "href"):
                    normalized = str(item.get(key) or "").strip()
                    if normalized.startswith(("http://", "https://")):
                        urls.append(normalized)
                text_candidate = str(item.get("text") or "").strip()
                if text_candidate.startswith(("http://", "https://")):
                    urls.append(text_candidate)
                return
            if isinstance(item, list):
                for nested in item:
                    collect(nested)

        collect(value)
        deduped: list[str] = []
        seen: set[str] = set()
        for item in urls:
            if item == "" or item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    def _parse_float_field_value(self, value: Any) -> float:
        """把飞书字段值稳妥转成浮点数。"""

        if isinstance(value, (int, float)):
            return float(value)
        text = self._stringify_field_value(value)
        if text == "":
            return 0.0
        try:
            return float(text)
        except Exception:
            return 0.0

    def _parse_int_field_value(self, value: Any) -> int:
        """把飞书字段值稳妥转成整数。"""

        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        text = self._stringify_field_value(value)
        if text == "":
            return 0
        try:
            return int(float(text))
        except Exception:
            return 0

    def _feedback_fact_snapshot_dict(self, fact: SupportIssueFeedbackFact) -> dict[str, object]:
        """把反馈事实转换成用于差异比较的轻量快照。"""

        return {
            "question": fact.question,
            "progress_value": fact.progress_value,
            "ai_solution": fact.ai_solution,
            "related_links": fact.related_links,
            "feedback_result": fact.feedback_result,
            "feedback_final_answer": fact.feedback_final_answer,
            "feedback_comment": fact.feedback_comment,
            "confidence_score": fact.confidence_score,
            "retrieval_hit_count": fact.retrieval_hit_count,
            "question_category": fact.question_category,
        }

    def _diff_feedback_fact_snapshots(
        self,
        previous_snapshot: dict[str, object],
        current_snapshot: dict[str, object],
    ) -> list[str]:
        """返回反馈事实中真正发生变化的字段名。"""

        changed_fields: list[str] = []
        for key, current_value in current_snapshot.items():
            if previous_snapshot.get(key) != current_value:
                changed_fields.append(key)
        return changed_fields

    def _build_feedback_fact(
        self,
        *,
        agent: SupportIssueAgentConfig,
        record_id: str,
        fields: dict[str, Any],
        resolved_fields: dict[str, FeishuBitableFieldInfo],
        synced_at: datetime,
    ) -> SupportIssueFeedbackFact:
        """把一行飞书记录抽成结构化反馈事实。

        这里故意不依赖运行记录表，而是直接从飞书当前表状态恢复事实，
        这样人工改写后的最终真值会被完整捕捉下来。
        """

        question_field = resolved_fields["question"]
        answer_field = resolved_fields["answer"]
        progress_field = resolved_fields["progress"]
        feedback_result_field = resolved_fields["feedback_result"]
        feedback_final_answer_field = resolved_fields["feedback_final_answer"]
        feedback_comment_field = resolved_fields["feedback_comment"]
        confidence_field = resolved_fields["confidence"]
        hit_count_field = resolved_fields["hit_count"]

        question = self._stringify_field_value(fields.get(question_field.field_name))
        composed_query = self._compose_query(question=question, fields=fields)
        question_category = self._classify_question(composed_query if composed_query != "" else question)
        related_links = self._extract_urls_from_field_value(fields.get(resolved_fields["link"].field_name))

        return SupportIssueFeedbackFact(
            id=str(uuid4()),
            agent_id=agent.id,
            record_id=record_id,
            question=question,
            progress_value=self._stringify_field_value(fields.get(progress_field.field_name)),
            ai_solution=self._stringify_field_value(fields.get(answer_field.field_name)),
            related_links=related_links,
            feedback_result=self._stringify_field_value(fields.get(feedback_result_field.field_name)),
            feedback_final_answer=self._stringify_field_value(fields.get(feedback_final_answer_field.field_name)),
            feedback_comment=self._stringify_field_value(fields.get(feedback_comment_field.field_name)),
            confidence_score=self._parse_float_field_value(fields.get(confidence_field.field_name)),
            retrieval_hit_count=self._parse_int_field_value(fields.get(hit_count_field.field_name)),
            question_category=question_category,
            source_bitable_url=agent.feishu_bitable_url,
            created_at=synced_at,
            updated_at=synced_at,
            last_synced_at=synced_at,
        )

    def _notify_support_owner_for_manual_review(
        self,
        *,
        agent: SupportIssueAgentConfig,
        run_id: str,
        record_id: str,
        question: str,
        module_value: str,
        solution: str,
    ) -> None:
        owner_user_id = self._resolve_support_owner_user_id(
            module_value=module_value,
            rules=agent.support_owner_rules,
            fallback_user_id=agent.fallback_support_yht_user_id,
        )
        if owner_user_id == "":
            self._record_notification_event(
                agent_id=agent.id,
                record_id=record_id,
                event_type="manual_review_assigned",
                recipient_user_id="",
                status="skipped",
                error_message="模块未匹配到负责人，且未配置兜底负责人。",
            )
            return

        topic = self._question_topic(question)
        title = f"支持问题待人工确认｜{module_value or '未分类模块'}"
        content = (
            f"模块：{module_value or '未分类模块'}\n"
            f"问题：{topic}\n"
            f"当前进度：{MANUAL_REVIEW_PROGRESS_VALUE}\n"
            f"处理摘要：{self._short_text(solution or NO_HIT_MESSAGE)}\n"
            f"飞书表：{agent.feishu_bitable_url}\n"
            f"record_id：{record_id}"
        )
        self._send_yonyou_notification(
            agent_id=agent.id,
            record_id=record_id,
            event_type="manual_review_assigned",
            recipient_user_ids=[owner_user_id],
            src_msg_id=f"SUPPORT_MANUAL_REVIEW:{agent.id}:{record_id}:{run_id}",
            title=title,
            content=content,
            web_url=agent.feishu_bitable_url,
        )

    def _backfill_support_owner_notification_if_needed(
        self,
        *,
        agent: SupportIssueAgentConfig,
        record_id: str,
        fields: dict[str, Any],
        resolved_fields: dict[str, FeishuBitableFieldInfo],
        fact: SupportIssueFeedbackFact,
        synced_at: datetime,
    ) -> None:
        if fact.progress_value != MANUAL_REVIEW_PROGRESS_VALUE:
            return

        module_field = resolved_fields["module"]
        module_value = self._stringify_field_value(fields.get(module_field.field_name))
        owner_user_id = self._resolve_support_owner_user_id(
            module_value=module_value,
            rules=agent.support_owner_rules,
            fallback_user_id=agent.fallback_support_yht_user_id,
        )
        if owner_user_id != "" and self._has_notification_event_for_recipient(
            agent_id=agent.id,
            record_id=record_id,
            event_type="manual_review_assigned",
            recipient_user_id=owner_user_id,
            statuses=("sent",),
        ):
            return
        self._notify_support_owner_for_manual_review(
            agent=agent,
            run_id=f"sync-{synced_at.strftime('%Y%m%d%H%M%S')}",
            record_id=record_id,
            question=fact.question,
            module_value=module_value,
            solution=fact.ai_solution or NO_HIT_MESSAGE,
        )

    def _notify_registrants_for_confirmation_completed(
        self,
        *,
        agent: SupportIssueAgentConfig,
        record_id: str,
        fields: dict[str, Any],
        resolved_fields: dict[str, FeishuBitableFieldInfo],
        fact: SupportIssueFeedbackFact,
        progress_changed_at: datetime,
    ) -> None:
        registrant_field = resolved_fields["registrant"]
        try:
            registrant_user_ids = self._extract_registrant_user_ids(fields=fields, registrant_field=registrant_field)
        except YonyouContactsSearchError as exc:
            self._record_notification_event(
                agent_id=agent.id,
                record_id=record_id,
                event_type="registrant_confirmed",
                recipient_user_id="",
                status="failed",
                error_message=f"登记人联系人查询失败：{exc}",
            )
            return
        if len(registrant_user_ids) == 0:
            self._record_notification_event(
                agent_id=agent.id,
                record_id=record_id,
                event_type="registrant_confirmed",
                recipient_user_id="",
                status="skipped",
                error_message=f"登记人列“{registrant_field.field_name}”未提取到有效邮箱/短账号，或未查询到对应 userId。",
            )
            return

        topic = self._question_topic(fact.question)
        final_summary = fact.feedback_final_answer.strip() or fact.ai_solution.strip() or "请到支持问题表查看处理结果。"
        content = (
            f"提示您登记的问题「{topic}」已经回复，请去支持问题表格查看。\n"
            f"当前进度：{HUMAN_CONFIRMED_PROGRESS_VALUE}\n"
            f"处理结果：{self._short_text(final_summary, limit=180)}\n"
            f"{self._registrant_notification_reminder()}\n"
            f"飞书表：{agent.feishu_bitable_url}\n"
            f"record_id：{record_id}"
        )
        self._send_yonyou_notification(
            agent_id=agent.id,
            record_id=record_id,
            event_type="registrant_confirmed",
            recipient_user_ids=registrant_user_ids,
            src_msg_id=f"SUPPORT_CONFIRM_DONE:{agent.id}:{record_id}:{progress_changed_at.isoformat()}",
            title=f"支持问题已回复｜{topic}",
            content=content,
            web_url=agent.feishu_bitable_url,
        )

    def _backfill_registrant_confirmation_notification_if_needed(
        self,
        *,
        agent: SupportIssueAgentConfig,
        record_id: str,
        fields: dict[str, Any],
        resolved_fields: dict[str, FeishuBitableFieldInfo],
        fact: SupportIssueFeedbackFact,
        synced_at: datetime,
    ) -> None:
        if fact.progress_value != HUMAN_CONFIRMED_PROGRESS_VALUE:
            return
        try:
            registrant_user_ids = self._extract_registrant_user_ids(
                fields=fields,
                registrant_field=resolved_fields["registrant"],
            )
        except YonyouContactsSearchError as exc:
            self._record_notification_event(
                agent_id=agent.id,
                record_id=record_id,
                event_type="registrant_confirmed",
                recipient_user_id="",
                status="failed",
                error_message=f"登记人联系人查询失败：{exc}",
            )
            return
        if len(registrant_user_ids) > 0 and all(
            self._has_notification_event_for_recipient(
                agent_id=agent.id,
                record_id=record_id,
                event_type="registrant_confirmed",
                recipient_user_id=recipient_user_id,
                statuses=("sent",),
            )
            for recipient_user_id in registrant_user_ids
        ):
            return
        self._notify_registrants_for_confirmation_completed(
            agent=agent,
            record_id=record_id,
            fields=fields,
            resolved_fields=resolved_fields,
            fact=fact,
            progress_changed_at=synced_at,
        )

    def _feedback_fact_to_candidate(
        self,
        fact: SupportIssueFeedbackFact,
    ) -> SupportIssueCaseCandidate:
        """把已采纳的反馈事实转换为案例候选。"""

        return SupportIssueCaseCandidate(
            id=str(uuid4()),
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
            created_at=fact.last_synced_at,
            updated_at=fact.last_synced_at,
        )

    def _should_create_case_candidate(self, fact: SupportIssueFeedbackFact) -> bool:
        """判断当前反馈事实是否应进入案例候选池。"""

        return (
            fact.feedback_result in {FEEDBACK_ACCEPTED, FEEDBACK_REVISED_ACCEPTED}
            and fact.feedback_final_answer.strip() != ""
        )

    def _case_candidate_payload_changed(
        self,
        current: SupportIssueCaseCandidate,
        next_candidate: SupportIssueCaseCandidate,
    ) -> bool:
        """判断候选内容是否发生了需要重新审核的变化。"""

        comparable_pairs = (
            (current.question, next_candidate.question),
            (current.ai_draft, next_candidate.ai_draft),
            (current.feedback_result, next_candidate.feedback_result),
            (current.final_solution, next_candidate.final_solution),
            (current.feedback_comment, next_candidate.feedback_comment),
            (current.confidence_score, next_candidate.confidence_score),
            (current.retrieval_hit_count, next_candidate.retrieval_hit_count),
            (current.question_category, next_candidate.question_category),
            (current.related_links, next_candidate.related_links),
        )
        return any(left != right for left, right in comparable_pairs)

    def _normalize_case_library_category(self, question_category: str, question: str) -> str:
        """把当前问题分类映射到正式案例库节点。

        这里单独做一次归一化，是因为运行时分类更偏“回答策略”，
        而案例库分类更偏“知识运营目录”。
        """

        normalized_category = SUPPORT_CASE_LIBRARY_CATEGORY_MAP.get(question_category)
        if normalized_category:
            if normalized_category == SUPPORT_CASE_LIBRARY_DEFAULT_CATEGORY:
                lowered_question = question.lower()
                if any(token in lowered_question for token in ("性能", "内存", "cpu", "heap", "oom")):
                    return "性能问题"
            return normalized_category
        return SUPPORT_CASE_LIBRARY_DEFAULT_CATEGORY

    def _build_case_title(self, question: str, record_id: str) -> str:
        """生成案例标题，优先取问题首行。"""

        first_line = question.strip().splitlines()[0].strip() if question.strip() != "" else ""
        if first_line == "":
            return f"支持案例-{record_id}"
        compact = re.sub(r"\s+", " ", first_line)
        return compact[:80]

    def _build_case_document_markdown(
        self,
        *,
        candidate: SupportIssueCaseCandidate,
        reviewer_name: str,
        approved_at: datetime,
    ) -> str:
        """把候选案例整理成标准知识文档内容。

        v1 不尝试把人工最终方案自动拆解成特别复杂的结构化字段，
        而是采用稳定、可复用的 Markdown 模板：
        - 头部固定字段便于检索和展示；
        - 核心方案完整保留，避免过度抽取造成知识损失。
        """

        related_links_block = (
            "\n".join(f"- {item}" for item in candidate.related_links)
            if len(candidate.related_links) > 0
            else "- 无"
        )
        feedback_comment = candidate.feedback_comment.strip() or "无"
        final_solution = candidate.final_solution.strip() or "无"
        ai_draft = candidate.ai_draft.strip() or "无"
        title = self._build_case_title(candidate.question, candidate.record_id)
        category = self._normalize_case_library_category(candidate.question_category, candidate.question)

        return (
            f"# {title}\n\n"
            f"## 问题标题\n{title}\n\n"
            f"## 问题现象\n{candidate.question.strip() or '无'}\n\n"
            f"## 适用范围\n- 分类：{category}\n- 来源 Agent：{candidate.agent_id}\n\n"
            f"## 排查步骤\n{ai_draft}\n\n"
            f"## 最终方案\n{final_solution}\n\n"
            f"## 注意事项\n{feedback_comment}\n\n"
            f"## 关联文档链接\n{related_links_block}\n\n"
            f"## 来源飞书记录\n- record_id：{candidate.record_id}\n- bitable_url：{candidate.source_bitable_url or '无'}\n\n"
            f"## 审核信息\n- 审核人：{reviewer_name}\n- 审核时间：{approved_at.astimezone(timezone.utc).isoformat()}\n"
        )

    def _ensure_case_library_node(self, *, category_name: str) -> str:
        """确保正式案例库节点存在，并返回目标分类节点 ID。"""

        root_node = self.knowledge_store.create_node(SUPPORT_CASE_LIBRARY_ROOT, ROOT_NODE_ID)
        category_node = self.knowledge_store.create_node(category_name, root_node.id)
        return category_node.id

    def _normalize_candidate_edit_text(self, raw_value: str | None, *, fallback: str) -> str:
        """规范候选编辑页提交的文本字段。

        这里统一约定：
        - 前端不传值，说明沿用当前内容；
        - 前端传空字符串，说明用户明确清空；
        - 最终统一做 `strip()`，避免尾部空格导致“看起来没改、实际上有 diff”。
        """

        if raw_value is None:
            return fallback
        return str(raw_value).strip()

    def _delete_case_candidate_document(self, candidate: SupportIssueCaseCandidate) -> None:
        """删除候选对应的正式知识库文档。

        两态化以后，已审核通过的案例一旦再次被人工改写，就必须立刻退出正式知识库，
        否则检索链路会继续命中过期版本，形成“候选页已经改了，但回答仍引用旧内容”的脏数据。
        """

        if (candidate.knowledge_document_id or "").strip() == "":
            return
        try:
            self.knowledge_store.delete_document(candidate.knowledge_document_id)
        except ValueError:
            # 旧文档可能已经被人工删除或历史数据不完整；这里不把整次编辑链路打断。
            return

    def _build_feedback_fact_from_candidate_edit(
        self,
        *,
        agent: SupportIssueAgentConfig,
        candidate: SupportIssueCaseCandidate,
        existing_fact: SupportIssueFeedbackFact | None,
        final_solution: str,
        feedback_comment: str,
        updated_at: datetime,
        synced_to_feishu: bool,
    ) -> SupportIssueFeedbackFact:
        """把候选页上的人工编辑结果回写成最新反馈事实。

        这样做的核心目的是保持三层数据一致：
        - 飞书表：业务同学可见；
        - feedback facts：平台内的最新真值；
        - case candidates：审核中的候选内容。
        """

        return SupportIssueFeedbackFact(
            id=str(uuid4()),
            agent_id=agent.id,
            record_id=candidate.record_id,
            question=candidate.question,
            progress_value=existing_fact.progress_value if existing_fact is not None else "",
            ai_solution=(existing_fact.ai_solution if existing_fact is not None else candidate.ai_draft) or candidate.ai_draft,
            related_links=list(candidate.related_links),
            feedback_result=(existing_fact.feedback_result if existing_fact is not None else candidate.feedback_result)
            or candidate.feedback_result,
            feedback_final_answer=final_solution,
            feedback_comment=feedback_comment,
            confidence_score=candidate.confidence_score,
            retrieval_hit_count=candidate.retrieval_hit_count,
            question_category=candidate.question_category,
            source_bitable_url=agent.feishu_bitable_url or candidate.source_bitable_url,
            created_at=existing_fact.created_at if existing_fact is not None else updated_at,
            updated_at=updated_at,
            last_synced_at=updated_at if synced_to_feishu or existing_fact is None else existing_fact.last_synced_at,
        )

    def _save_case_candidate_content(
        self,
        *,
        agent: SupportIssueAgentConfig,
        candidate: SupportIssueCaseCandidate,
        final_solution: str,
        feedback_comment: str,
        sync_to_feishu: bool,
    ) -> SupportIssueCaseCandidate:
        """保存候选页对“人工最终方案 / 反馈备注”的修改。

        状态流转规则固定为：
        - 内容没变：直接返回当前候选；
        - 内容有变：统一重置为 `pending_review`；
        - 如果原来已经 `approved`，先下线正式知识库旧文档，再保存新内容。
        """

        content_changed = (
            final_solution != candidate.final_solution
            or feedback_comment != candidate.feedback_comment
        )
        if not content_changed:
            return candidate

        if sync_to_feishu:
            resolved_fields = self._resolve_runtime_field_mapping(agent, records=[])
            update_fields = self._build_runtime_update_fields(
                (resolved_fields["feedback_final_answer"], final_solution),
                (resolved_fields["feedback_comment"], feedback_comment),
            )
            if len(update_fields) > 0:
                self._update_row_fields(
                    agent,
                    record_id=candidate.record_id,
                    fields=update_fields,
                )

        if candidate.status == "approved":
            self._delete_case_candidate_document(candidate)

        now = _utc_now()
        previous_fact = self.support_issue_store.get_feedback_fact(agent.id, candidate.record_id)
        next_fact = self._build_feedback_fact_from_candidate_edit(
            agent=agent,
            candidate=candidate,
            existing_fact=previous_fact,
            final_solution=final_solution,
            feedback_comment=feedback_comment,
            updated_at=now,
            synced_to_feishu=sync_to_feishu,
        )
        previous_snapshot = (
            self._feedback_fact_snapshot_dict(previous_fact)
            if previous_fact is not None
            else {}
        )
        current_snapshot = self._feedback_fact_snapshot_dict(next_fact)
        changed_fields = self._diff_feedback_fact_snapshots(previous_snapshot, current_snapshot)

        self.support_issue_store.upsert_feedback_fact(next_fact)
        if previous_fact is not None and len(changed_fields) > 0:
            self.support_issue_store.append_feedback_history(
                agent_id=agent.id,
                record_id=candidate.record_id,
                changed_fields=changed_fields,
                previous_snapshot=previous_snapshot,
                current_snapshot=current_snapshot,
                changed_at=now,
            )

        next_candidate = SupportIssueCaseCandidate(
            id=candidate.id,
            agent_id=candidate.agent_id,
            record_id=candidate.record_id,
            status="pending_review",
            question=candidate.question,
            ai_draft=candidate.ai_draft,
            feedback_result=candidate.feedback_result,
            final_solution=final_solution,
            feedback_comment=feedback_comment,
            confidence_score=candidate.confidence_score,
            retrieval_hit_count=candidate.retrieval_hit_count,
            question_category=candidate.question_category,
            related_links=list(candidate.related_links),
            source_bitable_url=agent.feishu_bitable_url or candidate.source_bitable_url,
            review_comment="",
            knowledge_document_id=None,
            approved_at=None,
            approved_by=None,
            created_at=candidate.created_at,
            updated_at=now,
        )
        return self.support_issue_store.upsert_case_candidate(
            next_candidate,
            reset_to_pending_review=True,
        )

    def _build_digest_period(self, now: datetime) -> tuple[datetime, datetime]:
        """生成当前 digest 的统计时间窗。

        v1 固定按最近 7 天做单 Agent 周报；
        手动立即汇总与定时周报共用这一窗口，保证口径一致。
        """

        return now - timedelta(days=7), now

    def _question_topic(self, question: str) -> str:
        """提取问题主题，避免无命中统计直接按整段长文本分桶。"""

        compact = " ".join(question.strip().splitlines()[:1]).strip()
        if compact == "":
            return "未命名问题"
        return compact[:80]

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
        return (
            f"本周期处理 {total_processed_count} 条问题；"
            f"AI分析完成 {generated_count} 条，待人工确认 {manual_review_count} 条，"
            f"无命中 {no_hit_count} 条，失败 {failed_count} 条；"
            f"直接采纳 {acceptance_count} 条，修改后采纳 {revised_acceptance_count} 条，驳回 {rejected_count} 条；"
            f"新增候选案例 {new_candidate_count} 条，审核通过入库 {approved_candidate_count} 条。"
        )

    def _format_digest_datetime(self, value: datetime) -> str:
        return value.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")

    def _build_digest_email_bodies(
        self,
        *,
        agent: SupportIssueAgentConfig,
        digest_run: SupportIssueDigestRun,
    ) -> tuple[str, str]:
        """生成 digest 的纯文本和 HTML 邮件正文。"""
        time_range = f"{self._format_digest_datetime(digest_run.period_start)} ~ {self._format_digest_datetime(digest_run.period_end)}"
        top_category_lines = [f"- {item.category}：{item.count}" for item in digest_run.top_categories] or ["- 无"]
        highlight_lines = [f"- {item}" for item in digest_run.highlight_samples] or ["- 无"]
        no_hit_lines = [f"- {item}" for item in digest_run.top_no_hit_topics] or ["- 无"]
        suggestion_lines = [f"- {item}" for item in digest_run.knowledge_gap_suggestions] or ["- 暂无"]
        top_category_text = "\n".join(top_category_lines)
        highlight_text = "\n".join(highlight_lines)
        no_hit_text = "\n".join(no_hit_lines)
        suggestion_text = "\n".join(suggestion_lines)

        body = (
            f"{agent.name} 汇总邮件\n\n"
            f"汇总类型：{'立即汇总' if digest_run.trigger_source == 'manual' else '周期汇总'}\n"
            f"统计时间范围：{time_range}（Asia/Shanghai）\n\n"
            f"【核心结论】\n{digest_run.summary}\n\n"
            f"【处理概览】\n"
            f"- 总处理量：{digest_run.total_processed_count}\n"
            f"- AI分析完成：{digest_run.generated_count}\n"
            f"- 待人工确认：{digest_run.manual_review_count}\n"
            f"- 无命中：{digest_run.no_hit_count}\n"
            f"- 失败：{digest_run.failed_count}\n\n"
            f"【人工反馈】\n"
            f"- 直接采纳：{digest_run.acceptance_count}\n"
            f"- 修改后采纳：{digest_run.revised_acceptance_count}\n"
            f"- 驳回：{digest_run.rejected_count}\n\n"
            f"【高频分类 Top 5】\n{top_category_text}\n\n"
            f"【高频无命中主题 Top 5】\n{no_hit_text}\n\n"
            f"【重点问题样本】\n{highlight_text}\n\n"
            f"【案例沉淀】\n"
            f"- 新增候选案例：{digest_run.new_candidate_count}\n"
            f"- 审核通过入库：{digest_run.approved_candidate_count}\n\n"
            f"【知识缺口建议】\n{suggestion_text}\n"
        )

        stat_cards = [
            ("总处理量", digest_run.total_processed_count),
            ("AI分析完成", digest_run.generated_count),
            ("待人工确认", digest_run.manual_review_count),
            ("无命中", digest_run.no_hit_count),
            ("失败", digest_run.failed_count),
            ("直接采纳", digest_run.acceptance_count + digest_run.revised_acceptance_count),
        ]
        cards_html = "".join(
            (
                "<div style=\"flex:1 1 160px;border:1px solid #dbe7f3;border-radius:14px;padding:14px 16px;"
                "background:#f8fbff;min-width:140px;\">"
                f"<div style=\"font-size:12px;color:#5f7286;\">{escape(label)}</div>"
                f"<div style=\"margin-top:8px;font-size:24px;font-weight:700;color:#102a43;\">{value}</div>"
                "</div>"
            )
            for label, value in stat_cards
        )

        def render_list(items: list[str]) -> str:
            return "".join(f"<li style=\"margin:6px 0;\">{escape(item)}</li>" for item in items)

        top_categories_html = "".join(
            f"<tr><td style=\"padding:8px 10px;border-bottom:1px solid #e5edf5;\">{escape(item.category)}</td>"
            f"<td style=\"padding:8px 10px;border-bottom:1px solid #e5edf5;text-align:right;\">{item.count}</td></tr>"
            for item in digest_run.top_categories
        ) or "<tr><td colspan=\"2\" style=\"padding:8px 10px;color:#6b7c93;\">无</td></tr>"

        html_body = (
            "<div style=\"font-family:'PingFang SC','Microsoft YaHei',sans-serif;background:#f4f7fb;padding:24px;color:#102a43;\">"
            "<div style=\"max-width:960px;margin:0 auto;background:#ffffff;border:1px solid #d9e2ec;border-radius:20px;overflow:hidden;\">"
            "<div style=\"padding:24px 28px;background:linear-gradient(135deg,#0f4c81,#1f7a8c);color:#ffffff;\">"
            f"<div style=\"font-size:13px;opacity:0.9;\">支持问题 Agent {'立即汇总' if digest_run.trigger_source == 'manual' else '周期汇总'}</div>"
            f"<h2 style=\"margin:8px 0 0;font-size:28px;\">{escape(agent.name)}</h2>"
            f"<div style=\"margin-top:10px;font-size:13px;opacity:0.92;\">统计时间范围：{escape(time_range)}（Asia/Shanghai）</div>"
            "</div>"
            "<div style=\"padding:24px 28px;\">"
            "<h3 style=\"margin:0 0 12px;font-size:18px;\">核心结论</h3>"
            f"<div style=\"padding:16px 18px;border-radius:14px;background:#f8fbff;border:1px solid #dbe7f3;line-height:1.8;\">{escape(digest_run.summary)}</div>"
            "<div style=\"margin-top:18px;display:flex;flex-wrap:wrap;gap:12px;\">"
            f"{cards_html}"
            "</div>"
            "<div style=\"margin-top:24px;display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px;\">"
            "<div style=\"border:1px solid #e5edf5;border-radius:16px;padding:18px;\">"
            "<h3 style=\"margin:0 0 12px;font-size:16px;\">人工反馈</h3>"
            "<ul style=\"padding-left:18px;margin:0;color:#334e68;line-height:1.8;\">"
            f"{render_list([f'直接采纳：{digest_run.acceptance_count}', f'修改后采纳：{digest_run.revised_acceptance_count}', f'驳回：{digest_run.rejected_count}'])}"
            "</ul></div>"
            "<div style=\"border:1px solid #e5edf5;border-radius:16px;padding:18px;\">"
            "<h3 style=\"margin:0 0 12px;font-size:16px;\">案例沉淀</h3>"
            "<ul style=\"padding-left:18px;margin:0;color:#334e68;line-height:1.8;\">"
            f"{render_list([f'新增候选案例：{digest_run.new_candidate_count}', f'审核通过入库：{digest_run.approved_candidate_count}'])}"
            "</ul></div></div>"
            "<div style=\"margin-top:24px;border:1px solid #e5edf5;border-radius:16px;overflow:hidden;\">"
            "<div style=\"padding:14px 18px;background:#f8fbff;font-weight:600;\">高频分类 Top 5</div>"
            "<table style=\"width:100%;border-collapse:collapse;font-size:14px;color:#243b53;\">"
            "<thead><tr><th style=\"padding:10px;text-align:left;background:#fdfefe;border-bottom:1px solid #e5edf5;\">分类</th>"
            "<th style=\"padding:10px;text-align:right;background:#fdfefe;border-bottom:1px solid #e5edf5;\">次数</th></tr></thead>"
            f"<tbody>{top_categories_html}</tbody></table></div>"
            "<div style=\"margin-top:24px;display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px;\">"
            "<div style=\"border:1px solid #e5edf5;border-radius:16px;padding:18px;\">"
            "<h3 style=\"margin:0 0 12px;font-size:16px;\">高频无命中主题 Top 5</h3>"
            f"<ul style=\"padding-left:18px;margin:0;color:#334e68;line-height:1.8;\">{render_list(digest_run.top_no_hit_topics or ['无'])}</ul>"
            "</div>"
            "<div style=\"border:1px solid #e5edf5;border-radius:16px;padding:18px;\">"
            "<h3 style=\"margin:0 0 12px;font-size:16px;\">知识缺口建议</h3>"
            f"<ul style=\"padding-left:18px;margin:0;color:#334e68;line-height:1.8;\">{render_list(digest_run.knowledge_gap_suggestions or ['暂无'])}</ul>"
            "</div></div>"
            "<div style=\"margin-top:24px;border:1px solid #e5edf5;border-radius:16px;padding:18px;\">"
            "<h3 style=\"margin:0 0 12px;font-size:16px;\">重点问题样本</h3>"
            f"<ul style=\"padding-left:18px;margin:0;color:#334e68;line-height:1.8;\">{render_list(digest_run.highlight_samples or ['无'])}</ul>"
            "</div>"
            "</div></div></div>"
        )
        return body, html_body

    def list_agents(self) -> list[SupportIssueAgentConfig]:
        return self.support_issue_store.list_agents()

    def validate_bitable(self, request: FeishuBitableValidationRequest) -> FeishuBitableValidationResponse:
        return self.feishu_service.validate_bitable_url(request.feishu_bitable_url)

    def list_bitable_fields(self, request: FeishuBitableFieldsRequest) -> FeishuBitableFieldsResponse:
        try:
            parsed = self._build_bitable_context(request.feishu_bitable_url)
            raw_fields = self.feishu_service.list_bitable_fields(
                app_token=str(parsed["app_token"]),
                table_id=str(parsed["table_id"]),
            )
            fields = [field for item in raw_fields if (field := self._normalize_field_info(item)) is not None]
            source: str = "metadata_api"
            if len(fields) == 0:
                fields = self._fallback_fields_from_preview(
                    app_token=str(parsed["app_token"]),
                    table_id=str(parsed["table_id"]),
                )
                source = "preview_fallback"
            message = (
                f"已读取到 {len(fields)} 个字段。"
                if source == "metadata_api"
                else f"字段元数据接口未返回有效字段，已从表格数据预览推断出 {len(fields)} 个字段。"
            )
            return FeishuBitableFieldsResponse(
                **self._build_bitable_base_response(ok=True, message=message, parsed=parsed),
                fields=fields,
                source=source,
            )
        except ValueError as exc:
            return FeishuBitableFieldsResponse(ok=False, message=str(exc))
        except RuntimeError as exc:
            parsed_payload: dict[str, str | None] = {}
            try:
                parsed_payload = self._build_bitable_context(request.feishu_bitable_url)
            except ValueError:
                parsed_payload = {}
            return FeishuBitableFieldsResponse(
                **self._build_bitable_base_response(
                    ok=False,
                    message=self.feishu_service._friendly_validation_message(str(exc)),
                    parsed=parsed_payload,
                ),
                fields=[],
            )

    def list_pending_analysis_rows(
        self,
        request: FeishuBitablePendingAnalysisRequest,
    ) -> FeishuBitablePendingAnalysisResponse:
        try:
            parsed = self._build_bitable_context(request.feishu_bitable_url)
            records = self.feishu_service.list_bitable_records(
                app_token=str(parsed["app_token"]),
                table_id=str(parsed["table_id"]),
            )
            raw_fields = self.feishu_service.list_bitable_fields(
                app_token=str(parsed["app_token"]),
                table_id=str(parsed["table_id"]),
            )
            normalized_fields = [field for item in raw_fields if (field := self._normalize_field_info(item)) is not None]
            progress_field_name = request.progress_field_name.strip() or PENDING_ANALYSIS_FIELD_NAME
            available_field_names = {field.field_name for field in normalized_fields}
            if len(available_field_names) > 0 and progress_field_name not in available_field_names:
                available_text = "、".join(sorted(available_field_names))
                return FeishuBitablePendingAnalysisResponse(
                    **self._build_bitable_base_response(
                        ok=False,
                        message=f"当前表中未找到字段“{progress_field_name}”。可用字段：{available_text}",
                        parsed=parsed,
                    ),
                    filter_field_name=progress_field_name,
                    total_count=len(records),
                    matched_count=0,
                    rows=[],
                )

            content_field_name = self._find_content_field_name(records, normalized_fields)
            matched_rows: list[FeishuBitablePendingAnalysisRow] = []
            for item in records:
                fields = item.get("fields")
                if not isinstance(fields, dict):
                    continue
                progress_value = self._stringify_field_value(fields.get(progress_field_name))
                if progress_value != PENDING_ANALYSIS_FIELD_VALUE:
                    continue
                content = self._stringify_field_value(fields.get(content_field_name)) if content_field_name else ""
                matched_rows.append(
                    FeishuBitablePendingAnalysisRow(
                        record_id=str(item.get("record_id") or item.get("recordId") or "").strip(),
                        content=content,
                        fields=fields,
                    )
                )

            return FeishuBitablePendingAnalysisResponse(
                **self._build_bitable_base_response(
                    ok=True,
                    message=f"已筛选出 {len(matched_rows)} 条“{PENDING_ANALYSIS_FIELD_VALUE}”数据。",
                    parsed=parsed,
                ),
                filter_field_name=progress_field_name,
                content_field_name=content_field_name,
                total_count=len(records),
                matched_count=len(matched_rows),
                rows=matched_rows,
            )
        except ValueError as exc:
            return FeishuBitablePendingAnalysisResponse(ok=False, message=str(exc))
        except RuntimeError as exc:
            parsed_payload: dict[str, str | None] = {}
            try:
                parsed_payload = self._build_bitable_context(request.feishu_bitable_url)
            except ValueError:
                parsed_payload = {}
            return FeishuBitablePendingAnalysisResponse(
                **self._build_bitable_base_response(
                    ok=False,
                    message=self.feishu_service._friendly_validation_message(str(exc)),
                    parsed=parsed_payload,
                ),
                rows=[],
            )

    def preview_bitable(self, request: FeishuBitablePreviewRequest) -> FeishuBitablePreviewResponse:
        try:
            parsed = self._build_bitable_context(request.feishu_bitable_url)
            page = self.feishu_service.list_bitable_records_page(
                app_token=str(parsed["app_token"]),
                table_id=str(parsed["table_id"]),
                page_size=5,
            )
            preview_rows: list[dict[str, Any]] = []
            for item in page["items"]:
                preview_rows.append(
                    {
                        "record_id": str(item.get("record_id") or item.get("recordId") or "").strip(),
                        "fields": item.get("fields") if isinstance(item.get("fields"), dict) else {},
                    }
                )
            message = "表为空，但地址可用。" if len(preview_rows) == 0 else f"已拉取前 {len(preview_rows)} 行预览。"
            return FeishuBitablePreviewResponse(
                **self._build_bitable_base_response(ok=True, message=message, parsed=parsed),
                preview_rows=preview_rows,
                preview_count=len(preview_rows),
                has_more=bool(page["has_more"]),
            )
        except ValueError as exc:
            return FeishuBitablePreviewResponse(ok=False, message=str(exc))
        except RuntimeError as exc:
            parsed_payload: dict[str, str | None] = {}
            try:
                parsed_payload = self._build_bitable_context(request.feishu_bitable_url)
            except ValueError:
                parsed_payload = {}
            return FeishuBitablePreviewResponse(
                **self._build_bitable_base_response(
                    ok=False,
                    message=self.feishu_service._friendly_validation_message(str(exc)),
                    parsed=parsed_payload,
                ),
            )

    def validate_bitable_write(self, request: FeishuBitableWriteValidationRequest) -> FeishuBitableWriteValidationResponse:
        try:
            parsed = self._build_bitable_context(request.feishu_bitable_url)
            raw_fields = self.feishu_service.list_bitable_fields(
                app_token=str(parsed["app_token"]),
                table_id=str(parsed["table_id"]),
            )
            normalized_fields = [field for item in raw_fields if (field := self._normalize_field_info(item)) is not None]
            if len(normalized_fields) == 0:
                normalized_fields = self._fallback_fields_from_preview(
                    app_token=str(parsed["app_token"]),
                    table_id=str(parsed["table_id"]),
                )

            create_field, update_field = self._resolve_write_validation_fields(
                fields=normalized_fields,
                question_field_name=request.question_field_name,
                answer_field_name=request.answer_field_name,
                status_field_name=request.status_field_name,
            )

            created_fields = {
                create_field.field_name: "【飞书编辑验证】这是一条用于验证写权限的临时测试记录",
            }
            updated_fields = {
                update_field.field_name: "【飞书编辑验证】更新成功，当前飞书表格具备写入权限。",
            }

            created_record = self.feishu_service.create_bitable_record(
                app_token=str(parsed["app_token"]),
                table_id=str(parsed["table_id"]),
                fields=created_fields,
            )
            created_record_id = str(created_record.get("record_id") or created_record.get("recordId") or "").strip() or None
            if created_record_id is None:
                raise RuntimeError("创建测试行成功，但没有拿到 record_id。")

            updated_record: dict[str, Any] | None = None
            update_error: str | None = None
            delete_error: str | None = None
            try:
                updated_record = self.feishu_service.update_bitable_record(
                    app_token=str(parsed["app_token"]),
                    table_id=str(parsed["table_id"]),
                    record_id=created_record_id,
                    fields=updated_fields,
                )
            except Exception as exc:
                update_error = str(exc).strip() or "未知更新错误"
            finally:
                try:
                    self.feishu_service.delete_bitable_record(
                        app_token=str(parsed["app_token"]),
                        table_id=str(parsed["table_id"]),
                        record_id=created_record_id,
                    )
                except Exception as exc:
                    delete_error = str(exc).strip() or "未知删除错误"

            if update_error is not None and delete_error is not None:
                return FeishuBitableWriteValidationResponse(
                    **self._build_bitable_base_response(
                        ok=False,
                        message=(
                            f"测试行已创建，但更新失败，且删除清理也失败，请手工清理 record_id={created_record_id}。"
                            f"更新原因：{self._friendly_write_validation_message(update_error)}；"
                            f"删除原因：{delete_error}"
                        ),
                        parsed=parsed,
                    ),
                    created_record_id=created_record_id,
                    used_create_field_name=create_field.field_name,
                    used_update_field_name=update_field.field_name,
                    created_fields_preview=self._extract_record_fields(created_record, created_fields),
                )

            if update_error is not None:
                return FeishuBitableWriteValidationResponse(
                    **self._build_bitable_base_response(
                        ok=False,
                        message=f"测试行已创建，但更新失败。原因：{self._friendly_write_validation_message(update_error)}",
                        parsed=parsed,
                    ),
                    created_record_id=created_record_id,
                    deleted_record_id=created_record_id if delete_error is None else None,
                    used_create_field_name=create_field.field_name,
                    used_update_field_name=update_field.field_name,
                    created_fields_preview=self._extract_record_fields(created_record, created_fields),
                )

            if delete_error is not None:
                return FeishuBitableWriteValidationResponse(
                    **self._build_bitable_base_response(
                        ok=False,
                        message=f"创建和更新测试行已成功，但删除测试行失败，请手工清理 record_id={created_record_id}。原因：{delete_error}",
                        parsed=parsed,
                    ),
                    created_record_id=created_record_id,
                    updated_record_id=created_record_id if updated_record is not None else None,
                    used_create_field_name=create_field.field_name,
                    used_update_field_name=update_field.field_name,
                    created_fields_preview=self._extract_record_fields(created_record, created_fields),
                    updated_fields_preview=self._extract_record_fields(updated_record, updated_fields),
                )

            return FeishuBitableWriteValidationResponse(
                **self._build_bitable_base_response(
                    ok=True,
                    message=f"编辑验证成功：已使用字段“{create_field.field_name}”创建、字段“{update_field.field_name}”更新，并完成删除。",
                    parsed=parsed,
                ),
                created_record_id=created_record_id,
                updated_record_id=created_record_id,
                deleted_record_id=created_record_id,
                used_create_field_name=create_field.field_name,
                used_update_field_name=update_field.field_name,
                created_fields_preview=self._extract_record_fields(created_record, created_fields),
                updated_fields_preview=self._extract_record_fields(updated_record, updated_fields),
            )
        except ValueError as exc:
            return FeishuBitableWriteValidationResponse(ok=False, message=str(exc))
        except RuntimeError as exc:
            parsed_payload: dict[str, str | None] = {}
            try:
                parsed_payload = self._build_bitable_context(request.feishu_bitable_url)
            except ValueError:
                parsed_payload = {}
            return FeishuBitableWriteValidationResponse(
                **self._build_bitable_base_response(
                    ok=False,
                    message=self._friendly_write_validation_message(str(exc)),
                    parsed=parsed_payload,
                ),
            )
        except Exception as exc:
            parsed_payload: dict[str, str | None] = {}
            try:
                parsed_payload = self._build_bitable_context(request.feishu_bitable_url)
            except ValueError:
                parsed_payload = {}
            return FeishuBitableWriteValidationResponse(
                **self._build_bitable_base_response(
                    ok=False,
                    message=self._friendly_write_validation_message(str(exc).strip() or "未知错误"),
                    parsed=parsed_payload,
                ),
            )

    def get_agent(self, agent_id: str) -> SupportIssueAgentConfig:
        agent = self.support_issue_store.get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Support issue agent not found")
        return agent

    def create_agent(self, request: CreateSupportIssueAgentRequest) -> SupportIssueAgentConfig:
        model_config = self._require_runnable_model_config(request.model_settings)
        try:
            parsed_bitable = self.feishu_service.parse_bitable_url(request.feishu_bitable_url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return self.support_issue_store.create_agent(
            name=request.name,
            description=request.description,
            enabled=request.enabled,
            poll_interval_minutes=request.poll_interval_minutes,
            feishu_bitable_url=str(parsed_bitable["normalized_url"]),
            feishu_app_token=str(parsed_bitable["app_token"]),
            feishu_table_id=str(parsed_bitable["table_id"]),
            model_config=model_config,
            knowledge_scope_type=request.knowledge_scope_type,
            knowledge_scope_id=request.knowledge_scope_id,
            question_field_name=request.question_field_name,
            answer_field_name=request.answer_field_name,
            link_field_name=request.link_field_name,
            progress_field_name=request.progress_field_name,
            status_field_name=request.status_field_name,
            module_field_name=request.module_field_name,
            registrant_field_name=request.registrant_field_name,
            feedback_result_field_name=request.feedback_result_field_name,
            feedback_final_answer_field_name=request.feedback_final_answer_field_name,
            feedback_comment_field_name=request.feedback_comment_field_name,
            confidence_field_name=request.confidence_field_name,
            hit_count_field_name=request.hit_count_field_name,
            support_owner_rules=request.support_owner_rules,
            fallback_support_yht_user_id=request.fallback_support_yht_user_id,
            digest_enabled=request.digest_enabled,
            digest_recipient_emails=request.digest_recipient_emails,
            case_review_enabled=request.case_review_enabled,
        )

    def update_agent(self, agent_id: str, request: UpdateSupportIssueAgentRequest) -> SupportIssueAgentConfig:
        current = self.support_issue_store.get_agent(agent_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Support issue agent not found")

        parsed_bitable_url: str | None = None
        parsed_app_token: str | None = None
        parsed_table_id: str | None = None
        if request.feishu_bitable_url is not None:
            try:
                parsed_bitable = self.feishu_service.parse_bitable_url(request.feishu_bitable_url)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            parsed_bitable_url = str(parsed_bitable["normalized_url"])
            parsed_app_token = str(parsed_bitable["app_token"])
            parsed_table_id = str(parsed_bitable["table_id"])

        updated = self.support_issue_store.update_agent(
            agent_id,
            name=request.name,
            description=request.description,
            enabled=request.enabled,
            poll_interval_minutes=request.poll_interval_minutes,
            feishu_bitable_url=parsed_bitable_url,
            feishu_app_token=parsed_app_token,
            feishu_table_id=parsed_table_id,
            knowledge_scope_type=request.knowledge_scope_type,
            knowledge_scope_id=request.knowledge_scope_id,
            question_field_name=request.question_field_name,
            answer_field_name=request.answer_field_name,
            link_field_name=request.link_field_name,
            progress_field_name=request.progress_field_name,
            status_field_name=request.status_field_name,
            module_field_name=request.module_field_name,
            registrant_field_name=request.registrant_field_name,
            feedback_result_field_name=request.feedback_result_field_name,
            feedback_final_answer_field_name=request.feedback_final_answer_field_name,
            feedback_comment_field_name=request.feedback_comment_field_name,
            confidence_field_name=request.confidence_field_name,
            hit_count_field_name=request.hit_count_field_name,
            support_owner_rules=request.support_owner_rules,
            fallback_support_yht_user_id=request.fallback_support_yht_user_id,
            digest_enabled=request.digest_enabled,
            digest_recipient_emails=request.digest_recipient_emails,
            case_review_enabled=request.case_review_enabled,
            model_config=self._require_runnable_model_config(request.model_settings) if request.model_settings else None,
        )
        assert updated is not None
        return updated

    def list_runs(self, agent_id: str) -> list[SupportIssueRun]:
        self.get_agent(agent_id)
        return self.support_issue_store.list_runs(agent_id)

    def _safe_rate(self, numerator: int, denominator: int) -> float:
        if denominator <= 0:
            return 0.0
        return round(numerator / denominator, 4)

    def get_insights(self, agent_id: str) -> SupportIssueInsights:
        agent = self.get_agent(agent_id)
        runs = self.support_issue_store.list_runs(agent_id)[:100]

        total_processed_count = sum(run.processed_row_count for run in runs)
        generated_count = sum(run.generated_count for run in runs)
        manual_review_count = sum(run.manual_review_count for run in runs)
        no_hit_count = sum(run.no_hit_count for run in runs)
        failed_count = sum(run.failed_count for run in runs)

        category_counter: dict[str, int] = {}
        for run in runs:
            for row in run.row_results:
                if row.question_category.strip() == "":
                    continue
                category_counter[row.question_category] = category_counter.get(row.question_category, 0) + 1

        top_categories = [
            SupportIssueCategoryStat(category=category, count=count)
            for category, count in sorted(category_counter.items(), key=lambda item: item[1], reverse=True)[:5]
        ]

        acceptance_count = 0
        revised_acceptance_count = 0
        rejected_count = 0
        pending_confirm_count = 0
        try:
            records = self.feishu_service.list_bitable_records(
                app_token=agent.feishu_app_token,
                table_id=agent.feishu_table_id,
            )
            for item in records:
                fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
                feedback_result = self._stringify_field_value(fields.get(agent.feedback_result_field_name))
                if feedback_result == FEEDBACK_ACCEPTED:
                    acceptance_count += 1
                elif feedback_result == FEEDBACK_REVISED_ACCEPTED:
                    revised_acceptance_count += 1
                elif feedback_result == FEEDBACK_REJECTED:
                    rejected_count += 1
                elif feedback_result == FEEDBACK_PENDING:
                    pending_confirm_count += 1
        except Exception:
            pass

        analyzed_feedback_total = acceptance_count + revised_acceptance_count + rejected_count + pending_confirm_count
        low_confidence_rate = self._safe_rate(manual_review_count, max(total_processed_count, 1))
        no_hit_rate = self._safe_rate(no_hit_count, max(total_processed_count, 1))
        rejection_rate = self._safe_rate(rejected_count, max(analyzed_feedback_total, 1))
        acceptance_rate = self._safe_rate(
            acceptance_count + revised_acceptance_count,
            max(analyzed_feedback_total, 1),
        )
        manual_rewrite_rate = self._safe_rate(revised_acceptance_count, max(analyzed_feedback_total, 1))

        optimization_suggestions: list[str] = []
        if no_hit_rate >= 0.25:
            optimization_suggestions.append("无命中率偏高，建议优先补齐高频问题主题的知识文档并完善 external_url。")
        if low_confidence_rate >= 0.3:
            optimization_suggestions.append("低置信度转人工比例偏高，建议加强问题补充列填写规范并优化分类模板。")
        if rejection_rate >= 0.2:
            optimization_suggestions.append("人工驳回率偏高，建议重点复盘驳回样本并收敛回答边界。")
        if manual_rewrite_rate >= 0.25:
            optimization_suggestions.append("“修改后采纳”占比偏高，建议将人工改写步骤沉淀为可复用案例模板。")
        if len(top_categories) > 0:
            optimization_suggestions.append(f"当前高频问题类型为：{top_categories[0].category}，建议优先专项优化。")
        if len(optimization_suggestions) == 0:
            optimization_suggestions.append("当前整体效果稳定，建议持续积累人工反馈样本后再做策略微调。")

        return SupportIssueInsights(
            agent_id=agent_id,
            sample_run_count=len(runs),
            total_processed_count=total_processed_count,
            generated_count=generated_count,
            manual_review_count=manual_review_count,
            no_hit_count=no_hit_count,
            failed_count=failed_count,
            acceptance_count=acceptance_count,
            revised_acceptance_count=revised_acceptance_count,
            rejected_count=rejected_count,
            pending_confirm_count=pending_confirm_count,
            acceptance_rate=acceptance_rate,
            rejection_rate=rejection_rate,
            low_confidence_rate=low_confidence_rate,
            no_hit_rate=no_hit_rate,
            manual_rewrite_rate=manual_rewrite_rate,
            top_categories=top_categories,
            optimization_suggestions=optimization_suggestions,
        )

    def sync_feedback(self, agent_id: str) -> SupportIssueFeedbackSyncResponse:
        """从飞书同步反馈字段到平台数据库，并生成/刷新案例候选。

        这是反哺链路的第一步：
        1. 读飞书当前状态；
        2. upsert 最新反馈事实；
        3. 对关键字段变化写入历史；
        4. 根据采纳结果生成案例候选。
        """

        agent = self.get_agent(agent_id)
        synced_at = _utc_now()

        try:
            raw_rows = self.feishu_service.list_bitable_records(
                app_token=agent.feishu_app_token,
                table_id=agent.feishu_table_id,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"飞书反馈同步失败：{exc}") from exc

        resolved_fields = self._resolve_runtime_field_mapping(agent, records=raw_rows)
        fact_upsert_count = 0
        history_appended_count = 0
        candidate_created_count = 0
        candidate_updated_count = 0

        for row in raw_rows:
            record_id = self._extract_record_id(row)
            fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
            if record_id == "" or not isinstance(fields, dict):
                continue

            next_fact = self._build_feedback_fact(
                agent=agent,
                record_id=record_id,
                fields=fields,
                resolved_fields=resolved_fields,
                synced_at=synced_at,
            )
            previous_fact = self.support_issue_store.get_feedback_fact(agent.id, record_id)
            current_snapshot = self._feedback_fact_snapshot_dict(next_fact)
            previous_snapshot = (
                self._feedback_fact_snapshot_dict(previous_fact)
                if previous_fact is not None
                else {}
            )
            changed_fields = self._diff_feedback_fact_snapshots(previous_snapshot, current_snapshot)
            if previous_fact is not None and len(changed_fields) == 0:
                next_fact.updated_at = previous_fact.updated_at
                next_fact.created_at = previous_fact.created_at

            persisted_fact = self.support_issue_store.upsert_feedback_fact(next_fact)
            fact_upsert_count += 1

            if len(changed_fields) > 0 and previous_fact is not None:
                self.support_issue_store.append_feedback_history(
                    agent_id=agent.id,
                    record_id=record_id,
                    changed_fields=changed_fields,
                    previous_snapshot=previous_snapshot,
                    current_snapshot=current_snapshot,
                    changed_at=synced_at,
                )
                history_appended_count += 1

            self._backfill_support_owner_notification_if_needed(
                agent=agent,
                record_id=record_id,
                fields=fields,
                resolved_fields=resolved_fields,
                fact=persisted_fact,
                synced_at=synced_at,
            )

            self._backfill_registrant_confirmation_notification_if_needed(
                agent=agent,
                record_id=record_id,
                fields=fields,
                resolved_fields=resolved_fields,
                fact=persisted_fact,
                synced_at=synced_at,
            )

            current_candidate = self.support_issue_store.get_case_candidate_by_record(agent.id, record_id)

            next_candidate = self._feedback_fact_to_candidate(persisted_fact)
            should_create_candidate = agent.case_review_enabled and self._should_create_case_candidate(persisted_fact)

            if current_candidate is None:
                if not should_create_candidate:
                    continue
                self.support_issue_store.upsert_case_candidate(
                    next_candidate,
                    reset_to_pending_review=True,
                )
                candidate_created_count += 1
                continue

            candidate_changed = self._case_candidate_payload_changed(current_candidate, next_candidate)
            if candidate_changed:
                if current_candidate.status == "approved":
                    self._delete_case_candidate_document(current_candidate)
                self.support_issue_store.upsert_case_candidate(
                    next_candidate,
                    reset_to_pending_review=True,
                )
                candidate_updated_count += 1

        summary = (
            f"本次同步读取 {len(raw_rows)} 行；"
            f"更新反馈事实 {fact_upsert_count} 条，追加历史 {history_appended_count} 条，"
            f"新增候选 {candidate_created_count} 条，刷新候选 {candidate_updated_count} 条。"
        )
        return SupportIssueFeedbackSyncResponse(
            agent_id=agent.id,
            synced_row_count=len(raw_rows),
            fact_upsert_count=fact_upsert_count,
            history_appended_count=history_appended_count,
            candidate_created_count=candidate_created_count,
            candidate_updated_count=candidate_updated_count,
            summary=summary,
        )

    def list_case_candidates(
        self,
        agent_id: str,
        *,
        status: str | None = None,
        category: str | None = None,
        keyword: str | None = None,
    ) -> list[SupportIssueCaseCandidate]:
        self.get_agent(agent_id)
        return self.support_issue_store.list_case_candidates(
            agent_id,
            status=status,
            category=category,
            keyword=keyword,
        )

    def review_case_candidate(
        self,
        candidate_id: str,
        request: UpdateSupportIssueCaseCandidateRequest,
    ) -> SupportIssueCaseCandidate:
        """处理候选页上的保存与审核动作。"""

        candidate = self.support_issue_store.get_case_candidate(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="Support case candidate not found")

        agent = self.get_agent(candidate.agent_id)
        reviewer_name = request.reviewer_name.strip() or "平台管理员"
        review_comment = request.review_comment.strip()
        final_solution = self._normalize_candidate_edit_text(
            request.final_solution,
            fallback=candidate.final_solution,
        )
        feedback_comment = self._normalize_candidate_edit_text(
            request.feedback_comment,
            fallback=candidate.feedback_comment,
        )

        if request.action == "save_edit":
            return self._save_case_candidate_content(
                agent=agent,
                candidate=candidate,
                final_solution=final_solution,
                feedback_comment=feedback_comment,
                sync_to_feishu=request.sync_to_feishu,
            )

        content_changed = (
            final_solution != candidate.final_solution
            or feedback_comment != candidate.feedback_comment
        )
        if content_changed:
            candidate = self._save_case_candidate_content(
                agent=agent,
                candidate=candidate,
                final_solution=final_solution,
                feedback_comment=feedback_comment,
                sync_to_feishu=request.sync_to_feishu,
            )

        if (
            candidate.status == "approved"
            and not content_changed
            and (candidate.knowledge_document_id or "").strip() != ""
        ):
            return candidate

        if candidate.final_solution.strip() == "":
            raise HTTPException(status_code=400, detail="人工最终方案为空，不能直接通过并入库。")

        approved_at = _utc_now()
        category_name = self._normalize_case_library_category(candidate.question_category, candidate.question)
        target_node_id = self._ensure_case_library_node(category_name=category_name)
        document_title = self._build_case_title(candidate.question, candidate.record_id)
        file_name = f"{document_title}-{candidate.record_id}.md"
        document_content = self._build_case_document_markdown(
            candidate=candidate,
            reviewer_name=reviewer_name,
            approved_at=approved_at,
        )
        document = self.knowledge_store.ingest_document(
            file_name,
            document_content.encode("utf-8"),
            node_id=target_node_id,
            relative_path=f"{SUPPORT_CASE_LIBRARY_ROOT}/{category_name}/{file_name}",
        )
        updated = self.support_issue_store.update_case_candidate_review(
            candidate_id=candidate.id,
            status="approved",
            review_comment=review_comment,
            approved_by=reviewer_name,
            approved_at=approved_at,
            knowledge_document_id=document.id,
        )

        assert updated is not None
        return updated

    def list_digest_runs(self, agent_id: str) -> list[SupportIssueDigestRun]:
        self.get_agent(agent_id)
        return self.support_issue_store.list_digest_runs(agent_id)

    def run_digest(self, agent_id: str, *, trigger_source: str = "manual") -> SupportIssueDigestRun:
        """执行单 Agent digest，并发送周期邮件。"""

        agent = self.get_agent(agent_id)

        # digest 统计依赖平台内的反馈事实，因此这里先做一次同步，保证口径最新。
        self.sync_feedback(agent_id)

        started_at = _utc_now()
        period_start, period_end = self._build_digest_period(started_at)

        facts = self.support_issue_store.list_feedback_facts(agent_id)
        candidates = self.support_issue_store.list_case_candidates(agent_id)

        facts_in_period = [fact for fact in facts if fact.updated_at >= period_start]
        candidates_in_period = [candidate for candidate in candidates if candidate.updated_at >= period_start]
        approved_candidates_in_period = [
            candidate
            for candidate in candidates_in_period
            if candidate.status == "approved" and candidate.approved_at is not None and candidate.approved_at >= period_start
        ]

        generated_count = sum(1 for fact in facts_in_period if fact.progress_value == DONE_PROGRESS_VALUE)
        no_hit_facts = [fact for fact in facts_in_period if self._is_no_hit_feedback_fact(fact)]
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
            self._question_topic(fact.question)
            for fact in no_hit_facts
            if fact.question.strip() != ""
        )
        top_no_hit_topics = [topic for topic, _count in no_hit_topic_counter.most_common(5)]

        highlight_samples: list[str] = []
        for fact in sorted(facts_in_period, key=lambda item: item.updated_at, reverse=True)[:5]:
            highlight_samples.append(
                f"{self._question_topic(fact.question)}｜进度={fact.progress_value or '未知'}｜分类={fact.question_category or '未分类'}"
            )

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
            agent_id=agent.id,
            status="success",
            trigger_source=trigger_source if trigger_source in {"manual", "scheduled"} else "manual",
            started_at=started_at,
            ended_at=_utc_now(),
            period_start=period_start,
            period_end=period_end,
            recipient_emails=agent.digest_recipient_emails,
            email_sent=False,
            email_subject="",
            summary=self._build_digest_summary(
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
            acceptance_rate=self._safe_rate(acceptance_count + revised_acceptance_count, max(analyzed_feedback_total, 1)),
            rejection_rate=self._safe_rate(rejected_count, max(analyzed_feedback_total, 1)),
            low_confidence_rate=self._safe_rate(low_confidence_count, max(total_processed_count, 1)),
            no_hit_rate=self._safe_rate(no_hit_count, max(total_processed_count, 1)),
            manual_rewrite_rate=self._safe_rate(revised_acceptance_count, max(analyzed_feedback_total, 1)),
            top_categories=top_categories,
            top_no_hit_topics=top_no_hit_topics,
            highlight_samples=highlight_samples,
            knowledge_gap_suggestions=knowledge_gap_suggestions,
            new_candidate_count=len(candidates_in_period),
            approved_candidate_count=len(approved_candidates_in_period),
        )

        digest_run.email_subject = (
            f"【支持问题 Agent 立即汇总】{agent.name}"
            if digest_run.trigger_source == "manual"
            else f"【支持问题 Agent 周期汇总】{agent.name}"
        )
        body, html_body = self._build_digest_email_bodies(agent=agent, digest_run=digest_run)

        digest_items: list[dict[str, object]] = []
        for fact in facts_in_period:
            digest_items.append(
                {
                    "record_id": fact.record_id,
                    "item_type": "feedback_fact",
                    "title": self._question_topic(fact.question),
                    "payload": {
                        "progress_value": fact.progress_value,
                        "feedback_result": fact.feedback_result,
                        "question_category": fact.question_category,
                    },
                }
            )
        for candidate in candidates_in_period:
            digest_items.append(
                {
                    "record_id": candidate.record_id,
                    "candidate_id": candidate.id,
                    "item_type": "case_candidate",
                    "title": self._question_topic(candidate.question),
                    "payload": {
                        "status": candidate.status,
                        "question_category": candidate.question_category,
                    },
                }
            )

        try:
            self.mail_service.send_email(
                recipient_emails=agent.digest_recipient_emails,
                subject=digest_run.email_subject,
                body=body,
                html_body=html_body,
            )
            digest_run.email_sent = True
        except Exception as exc:
            digest_run.status = "failed"
            digest_run.error_message = str(exc)

        digest_run.ended_at = _utc_now()
        self.support_issue_store.record_digest_run(agent_id=agent.id, run=digest_run, items=digest_items)
        return digest_run

    def list_due_agents(self) -> list[SupportIssueAgentConfig]:
        return self.support_issue_store.list_due_agents()

    def list_due_digest_agents(self) -> list[SupportIssueAgentConfig]:
        return self.support_issue_store.list_due_digest_agents()

    def _update_row_fields(
        self,
        agent: SupportIssueAgentConfig,
        *,
        record_id: str,
        fields: dict[str, Any],
    ) -> None:
        self.feishu_service.update_bitable_record(
            app_token=agent.feishu_app_token,
            table_id=agent.feishu_table_id,
            record_id=record_id,
            fields=fields,
        )

    def run_agent(self, agent_id: str) -> SupportIssueRun:
        agent = self.get_agent(agent_id)
        started_at = _utc_now()
        run_id = str(uuid4())

        try:
            raw_rows = self.feishu_service.list_bitable_records(
                app_token=agent.feishu_app_token,
                table_id=agent.feishu_table_id,
            )
        except Exception as exc:
            failed_run = SupportIssueRun(
                id=run_id,
                agent_id=agent.id,
                status="failed",
                started_at=started_at,
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
            self.support_issue_store.record_run(agent.id, failed_run)
            return failed_run

        resolved_fields = self._resolve_runtime_field_mapping(agent, records=raw_rows)
        question_field = resolved_fields["question"]
        answer_field = resolved_fields["answer"]
        link_field = resolved_fields["link"]
        progress_field = resolved_fields["progress"]
        module_field = resolved_fields["module"]
        confidence_field = resolved_fields["confidence"]
        hit_count_field = resolved_fields["hit_count"]
        question_field_name = question_field.field_name
        answer_field_name = answer_field.field_name
        progress_field_name = progress_field.field_name
        link_is_url_like = self._is_url_like_field(link_field)
        historical_cases = self._collect_historical_cases(
            rows=raw_rows,
            question_field_name=question_field_name,
            agent=agent,
        )
        for approved_case in self.support_issue_store.list_approved_case_candidates(agent.id):
            if approved_case.question.strip() == "" or approved_case.final_solution.strip() == "":
                continue
            historical_cases.append(
                {
                    "question": approved_case.question,
                    "solution": approved_case.final_solution,
                    "feedback_result": approved_case.feedback_result or FEEDBACK_ACCEPTED,
                }
            )

        candidate_rows: list[dict[str, Any]] = []
        for item in raw_rows:
            fields = item.get("fields")
            if not isinstance(fields, dict):
                continue
            if self._row_needs_processing(progress_field_name, fields):
                candidate_rows.append(item)

        if len(candidate_rows) == 0:
            run = SupportIssueRun(
                id=run_id,
                agent_id=agent.id,
                status="no_change",
                started_at=started_at,
                ended_at=_utc_now(),
                fetched_row_count=len(raw_rows),
                processed_row_count=0,
                generated_count=0,
                manual_review_count=0,
                no_hit_count=0,
                failed_count=0,
                summary=f"本轮读取 {len(raw_rows)} 行，当前没有“{progress_field_name}”为待分析或失败待重试的数据。",
                error_message=None,
                row_results=[],
            )
            self.support_issue_store.record_run(agent.id, run)
            try:
                self.sync_feedback(agent.id)
            except Exception:
                pass
            return run

        generated_count = 0
        manual_review_count = 0
        no_hit_count = 0
        failed_count = 0
        row_results: list[SupportIssueRowResult] = []
        scope_type, scope_id = self._normalize_scope(agent.knowledge_scope_type, agent.knowledge_scope_id)

        for item in candidate_rows:
            record_id = str(item.get("record_id") or item.get("recordId") or "").strip()
            fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
            question = self._stringify_field_value(fields.get(question_field_name))
            module_value = self._stringify_field_value(fields.get(module_field.field_name))
            feedback_snapshot = self._extract_feedback_snapshot(agent, fields)
            composed_query = self._compose_query(question=question, fields=fields)
            category = self._classify_question(composed_query if composed_query != "" else question)
            similar_cases = self._select_similar_cases(query=composed_query or question, cases=historical_cases)
            similar_case_context = self._build_similar_case_context(similar_cases)
            if record_id == "":
                failed_count += 1
                row_results.append(
                    SupportIssueRowResult(
                        record_id="",
                        question=question,
                        status="failed",
                        solution="",
                        related_link=None,
                        message="当前行缺少 record_id，无法回写飞书。",
                        retrieval_hit_count=0,
                        confidence_score=0.0,
                        judge_status="failed",
                        judge_reason="缺少 record_id。",
                        question_category=category,
                        similar_case_count=len(similar_cases),
                        feedback_snapshot=feedback_snapshot,
                    )
                )
                continue

            try:
                self._update_row_fields(
                    agent,
                    record_id=record_id,
                    fields={progress_field_name: PROCESSING_PROGRESS_VALUE},
                )
            except Exception as exc:
                failed_count += 1
                row_results.append(
                    SupportIssueRowResult(
                        record_id=record_id,
                        question=question,
                        status="failed",
                        solution="",
                        related_link=None,
                        message=f"写入处理中状态失败：{exc}",
                        retrieval_hit_count=0,
                        confidence_score=0.0,
                        judge_status="failed",
                        judge_reason=f"写入处理中失败：{exc}",
                        question_category=category,
                        similar_case_count=len(similar_cases),
                        feedback_snapshot=feedback_snapshot,
                    )
                )
                continue

            if question == "":
                message = "问题列为空，无法生成解决方案。"
                try:
                    self._update_row_fields(
                        agent,
                        record_id=record_id,
                        fields=self._build_runtime_update_fields(
                            (answer_field, "生成失败：问题列为空，请补充后重试。"),
                            (link_field, self._empty_link_field_value(url_like_field=link_is_url_like)),
                            (confidence_field, 0.0),
                            (hit_count_field, 0),
                            (progress_field, FAILED_PROGRESS_VALUE),
                        ),
                    )
                except Exception as exc:
                    try:
                        self._mark_record_progress_only(
                            agent,
                            record_id=record_id,
                            progress_field_name=progress_field_name,
                            progress_value=FAILED_PROGRESS_VALUE,
                        )
                        message = f"{message} 详细失败信息回写失败，已仅更新回复进度为失败待重试：{exc}"
                    except Exception as progress_exc:
                        message = f"{message} 回写失败待重试状态也失败：{exc}；进度单独回写也失败：{progress_exc}"
                failed_count += 1
                row_results.append(
                    SupportIssueRowResult(
                        record_id=record_id,
                        question="",
                        status="failed",
                        solution="",
                        related_link=None,
                        message=message,
                        retrieval_hit_count=0,
                        confidence_score=0.0,
                        judge_status="failed",
                        judge_reason="问题列为空。",
                        question_category=category,
                        similar_case_count=len(similar_cases),
                        feedback_snapshot=feedback_snapshot,
                    )
                )
                continue

            try:
                system_prompt = self._compose_system_prompt(category)
                if similar_case_context != "":
                    system_prompt = (
                        system_prompt
                        + "以下历史已采纳案例仅作为辅助参考，不能覆盖知识依据；"
                        + similar_case_context
                    )
                retrieval_result = self.retrieval_service.run(
                    query=composed_query,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    model_config=agent.model_settings,
                    system_prompt=system_prompt,
                )
                retrieval_hit_count = len(retrieval_result.citations)
                if retrieval_hit_count == 0:
                    self._update_row_fields(
                        agent,
                        record_id=record_id,
                        fields=self._build_runtime_update_fields(
                            (answer_field, NO_HIT_MESSAGE),
                            (link_field, self._empty_link_field_value(url_like_field=link_is_url_like)),
                            (confidence_field, 0.0),
                            (hit_count_field, 0),
                            (progress_field, MANUAL_REVIEW_PROGRESS_VALUE),
                        ),
                    )
                    no_hit_count += 1
                    self._notify_support_owner_for_manual_review(
                        agent=agent,
                        run_id=run_id,
                        record_id=record_id,
                        question=question,
                        module_value=module_value,
                        solution=NO_HIT_MESSAGE,
                    )
                    row_results.append(
                        SupportIssueRowResult(
                            record_id=record_id,
                            question=question,
                            status="no_hit",
                            solution=NO_HIT_MESSAGE,
                            related_link=None,
                            message="未检索到可用知识，已标记待人工确认。",
                            retrieval_hit_count=0,
                            confidence_score=0.0,
                            judge_status="no_hit",
                            judge_reason="未命中知识，已转人工确认。",
                            question_category=category,
                            similar_case_count=len(similar_cases),
                            feedback_snapshot=feedback_snapshot,
                        )
                    )
                    continue

                solution = retrieval_result.summary.strip()
                judge_status, confidence_score, judge_reason = self._judge_solution(
                    question=question,
                    summary=solution,
                    retrieval_hit_count=retrieval_hit_count,
                )
                related_link = self._join_related_document_links(
                    retrieval_result,
                    url_like_field=link_is_url_like,
                )
                related_link_field_value = self._build_link_field_value(
                    retrieval_result,
                    url_like_field=link_is_url_like,
                )
                progress_value = DONE_PROGRESS_VALUE if judge_status == "pass" else MANUAL_REVIEW_PROGRESS_VALUE
                self._update_row_fields(
                    agent,
                    record_id=record_id,
                    fields=self._build_runtime_update_fields(
                        (answer_field, solution),
                        (link_field, related_link_field_value),
                        (confidence_field, round(confidence_score, 4)),
                        (hit_count_field, retrieval_hit_count),
                        (progress_field, progress_value),
                    ),
                )
                if judge_status == "pass":
                    generated_count += 1
                else:
                    manual_review_count += 1
                    self._notify_support_owner_for_manual_review(
                        agent=agent,
                        run_id=run_id,
                        record_id=record_id,
                        question=question,
                        module_value=module_value,
                        solution=solution,
                    )
                row_results.append(
                    SupportIssueRowResult(
                        record_id=record_id,
                        question=question,
                        status="generated" if judge_status == "pass" else "manual_review",
                        solution=solution,
                        related_link=related_link,
                        message=(
                            "已生成解决方案并回写飞书。"
                            if judge_status == "pass"
                            else f"已生成草稿答案，因置信度偏低转人工确认：{judge_reason}"
                        ),
                        retrieval_hit_count=retrieval_hit_count,
                        confidence_score=round(confidence_score, 4),
                        judge_status=judge_status,
                        judge_reason=judge_reason,
                        question_category=category,
                        similar_case_count=len(similar_cases),
                        feedback_snapshot=feedback_snapshot,
                    )
                )
            except Exception as exc:
                error_text = str(exc).strip() or "未知错误"
                failure_solution = f"生成失败：{error_text[:240]}"
                message = error_text
                try:
                    self._update_row_fields(
                        agent,
                        record_id=record_id,
                        fields=self._build_runtime_update_fields(
                            (answer_field, failure_solution),
                            (link_field, self._empty_link_field_value(url_like_field=link_is_url_like)),
                            (confidence_field, 0.0),
                            (hit_count_field, 0),
                            (progress_field, FAILED_PROGRESS_VALUE),
                        ),
                    )
                except Exception as update_exc:
                    try:
                        self._mark_record_progress_only(
                            agent,
                            record_id=record_id,
                            progress_field_name=progress_field_name,
                            progress_value=FAILED_PROGRESS_VALUE,
                        )
                        message = f"{message}；详细失败信息回写失败，已仅更新回复进度为失败待重试：{update_exc}"
                    except Exception as progress_exc:
                        message = f"{message}；回写失败待重试状态也失败：{update_exc}；进度单独回写也失败：{progress_exc}"
                failed_count += 1
                row_results.append(
                    SupportIssueRowResult(
                        record_id=record_id,
                        question=question,
                        status="failed",
                        solution=failure_solution,
                        related_link=None,
                        message=message,
                        retrieval_hit_count=0,
                        confidence_score=0.0,
                        judge_status="failed",
                        judge_reason=error_text[:200],
                        question_category=category,
                        similar_case_count=len(similar_cases),
                        feedback_snapshot=feedback_snapshot,
                    )
                )

        processed_count = len(candidate_rows)
        # 这里把整批行级结果汇总成一次 run 的总体状态：
        # 全失败 -> failed；部分失败 -> partial_success；其余 -> success。
        if failed_count > 0 and generated_count == 0 and manual_review_count == 0 and no_hit_count == 0:
            run_status = "failed"
        elif failed_count > 0:
            run_status = "partial_success"
        else:
            run_status = "success"

        summary = (
            f"本轮读取 {len(raw_rows)} 行，命中待处理 {len(candidate_rows)} 行；"
            f"已生成 {generated_count} 行，待人工确认 {manual_review_count} 行，"
            f"无命中 {no_hit_count} 行，失败 {failed_count} 行。"
        )
        run = SupportIssueRun(
            id=run_id,
            agent_id=agent.id,
            status=run_status,
            started_at=started_at,
            ended_at=_utc_now(),
            fetched_row_count=len(raw_rows),
            processed_row_count=processed_count,
            generated_count=generated_count,
            manual_review_count=manual_review_count,
            no_hit_count=no_hit_count,
            failed_count=failed_count,
            summary=summary,
            error_message=None if failed_count == 0 else "部分或全部问题处理失败，请查看行级摘要。",
            row_results=row_results,
        )
        self.support_issue_store.record_run(agent.id, run)
        try:
            # 这里不把同步失败升级为运行失败：
            # 主链路的核心职责是“读表 -> 检索 -> 回写”，
            # 反馈同步属于反哺增强层，失败时不应反向污染本次运行结果。
            self.sync_feedback(agent.id)
        except Exception:
            pass
        return run

"""Jira 重复工单审核 Agent 的业务服务。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import html
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request
from uuid import uuid4

import chromadb
from chromadb.config import Settings
from fastapi import HTTPException

from ..schemas import (
    CreateJiraDuplicateAgentRequest,
    JiraDuplicateAgentConfig,
    JiraDuplicateCandidate,
    JiraDuplicateFetchTestResponse,
    JiraDuplicateIssueResult,
    JiraDuplicateRun,
    JiraSolutionDraftReplyRequest,
    JiraSolutionDraftReplyResponse,
    JiraSolutionSearchRequest,
    JiraSolutionSearchResponse,
    ModelConfig,
    ParsedBug,
    UpdateJiraDuplicateAgentRequest,
)
from ..settings import AppSettings
from .embedding_service import EmbeddingService
from .jira_duplicate_store import JiraDuplicateStore
from .llm_service import LLMService
from .provider_store import ProviderStore
from .rag_embedding_settings_service import RAGEmbeddingSettingsService


COMPLETED_STATUSES = ("支持确认完成", "研发已完成", "已完成", "完成", "关闭", "已关闭")
VECTOR_COLLECTION_NAME = "jira_duplicate_cases"
DEFAULT_HIGH_SIMILARITY_THRESHOLD = 0.78
DEFAULT_MEDIUM_SIMILARITY_THRESHOLD = 0.55
MATCH_TEXT_NORMALIZER_VERSION = "public-noise-v2"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class _ScoreBucket:
    lexical_rank: int | None = None
    vector_rank: int | None = None
    lexical_score: float = 0.0
    vector_score: float = 0.0


@dataclass(frozen=True)
class _IssueSignature:
    objects: frozenset[str]
    actions: frozenset[str]
    symptoms: frozenset[str]
    environments: frozenset[str]
    clues: frozenset[str]
    domains: frozenset[str]


class JiraDuplicateService:
    """把 Jira 列表抓取、详情补齐、历史已完成工单匹配串成一条审核链路。"""

    def __init__(
        self,
        *,
        store: JiraDuplicateStore,
        llm_service: LLMService,
        settings: AppSettings,
        provider_store: ProviderStore | None = None,
        rag_embedding_settings_service: RAGEmbeddingSettingsService | None = None,
    ) -> None:
        self.store = store
        self.llm_service = llm_service
        self.settings = settings
        self.embedding_service = EmbeddingService(
            provider_store=provider_store,
            settings=settings,
            rag_embedding_settings_service=rag_embedding_settings_service,
        )
        self.vector_client = chromadb.PersistentClient(
            path=str(settings.chroma_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        self.vector_collection = self._get_or_create_vector_collection()

    def _get_or_create_vector_collection(self):
        return self.vector_client.get_or_create_collection(
            name=VECTOR_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    def _reset_vector_collection(self):
        try:
            self.vector_client.delete_collection(VECTOR_COLLECTION_NAME)
        except Exception:
            pass
        self.vector_collection = self._get_or_create_vector_collection()

    def list_agents(self) -> list[JiraDuplicateAgentConfig]:
        return self.store.list_agents()

    def get_agent(self, agent_id: str) -> JiraDuplicateAgentConfig:
        agent = self.store.get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="未找到 Jira 重复工单 Agent。")
        return agent

    def create_agent(self, request: CreateJiraDuplicateAgentRequest) -> JiraDuplicateAgentConfig:
        model_config = self.llm_service.resolve_model_config(request.model_settings)
        return self.store.create_agent(request, model_config)

    def update_agent(
        self,
        agent_id: str,
        request: UpdateJiraDuplicateAgentRequest,
    ) -> JiraDuplicateAgentConfig:
        model_config = (
            self.llm_service.normalize_model_config_reference(request.model_settings)
            if request.model_settings is not None
            else None
        )
        updated = self.store.update_agent(agent_id, request, model_config)
        if updated is None:
            raise HTTPException(status_code=404, detail="未找到 Jira 重复工单 Agent。")
        return updated

    def list_due_agents(self) -> list[JiraDuplicateAgentConfig]:
        return self.store.list_due_agents()

    def list_runs(self, agent_id: str) -> list[JiraDuplicateRun]:
        self.get_agent(agent_id)
        return self.store.list_runs(agent_id)

    def test_fetch(self, agent_id: str) -> JiraDuplicateFetchTestResponse:
        agent = self.get_agent(agent_id)
        result = self._execute_dashboard_request(
            dashboard_url=agent.dashboard_url,
            request_method=agent.request_method,
            request_headers=agent.request_headers,
            request_body_json=agent.request_body_json,
            request_body_text=agent.request_body_text,
        )
        parsed_preview: list[ParsedBug] = []
        parsed_payload = result.get("parsed_payload")
        if parsed_payload is not None:
            parsed_preview = self.llm_service.preview_bug_list_from_payload(parsed_payload)[:8]
        return JiraDuplicateFetchTestResponse(
            ok=bool(result["ok"]),
            status_code=int(result["status_code"]),
            message=str(result["message"]),
            dashboard_url=agent.dashboard_url,
            request_method=agent.request_method,
            request_headers=agent.request_headers,
            request_body_json=agent.request_body_json,
            request_body_text=agent.request_body_text,
            detail_url_template=agent.detail_url_template,
            detail_request_method=agent.detail_request_method,
            detail_request_headers=agent.detail_request_headers,
            detail_request_body_text=agent.detail_request_body_text,
            response_content_type=str(result["content_type"]),
            response_body_preview=str(result["response_body_preview"]),
            parsed_item_count=int(result["parsed_item_count"]),
            parsed_issue_count=len(parsed_preview),
            parsed_issue_preview=parsed_preview,
        )

    def run_agent(self, agent_id: str) -> JiraDuplicateRun:
        agent = self.get_agent(agent_id)
        started_at = _utc_now()
        try:
            self._ensure_case_index(agent)
            payload, fetched_count = self._fetch_current_jira_issues(agent)
            issues = self.llm_service.extract_bug_list(
                dashboard_payload=payload,
                model_config=agent.model_settings,
            )
            hydrated = self._hydrate_issue_details(agent, issues)
            results = [self._match_current_issue(agent, issue) for issue in hydrated]
            high_count = sum(1 for item in results if item.match_level == "high")
            medium_count = sum(1 for item in results if item.match_level == "medium")
            matched_count = high_count + medium_count
            no_match_count = sum(1 for item in results if item.match_level in {"low", "none"})
            status = "no_change" if len(results) == 0 else "success"
            summary = (
                f"拉取 {fetched_count} 条，解析 {len(results)} 条；"
                f"建议复用 {high_count} 条，需人工判断 {medium_count} 条，未命中 {no_match_count} 条。"
            )
            run = JiraDuplicateRun(
                id=str(uuid4()),
                agent_id=agent.id,
                status=status,
                started_at=started_at,
                ended_at=_utc_now(),
                fetched_count=fetched_count,
                parsed_count=len(results),
                matched_count=matched_count,
                high_confidence_count=high_count,
                medium_confidence_count=medium_count,
                no_match_count=no_match_count,
                failed_count=0,
                summary=summary,
                issue_results=results,
            )
        except Exception as exc:
            message = str(exc).strip() or "未知错误"
            run = JiraDuplicateRun(
                id=str(uuid4()),
                agent_id=agent.id,
                status="failed",
                started_at=started_at,
                ended_at=_utc_now(),
                summary=f"运行失败：{message[:240]}",
                error_message=message[:1000],
            )
        return self.store.record_run(agent, run)

    def reindex(self, agent_id: str) -> dict[str, Any]:
        agent = self.get_agent(agent_id)
        result = self.reindex_source_db(agent.source_db_path, agent_id=agent.id)
        return {
            "agent_id": agent.id,
            "indexed_count": result["indexed_count"],
            "embedding_backend": result["embedding_backend"],
        }

    def reindex_source_db(self, source_db_path: str, agent_id: str = "jira-data-source") -> dict[str, Any]:
        return self._reindex_cases(source_db_path=source_db_path, agent_id=agent_id)

    def search_solution(self, search_request: JiraSolutionSearchRequest) -> JiraSolutionSearchResponse:
        model_config = self.llm_service.resolve_model_config(search_request.model_settings)
        now = _utc_now()
        agent = JiraDuplicateAgentConfig(
            id="manual-solution-search",
            name="Jira 方案检索 Agent",
            description="开发人员粘贴问题描述后，检索历史已完成 Jira 解决方案。",
            source_db_path=search_request.source_db_path,
            dashboard_url="manual://jira-solution-search",
            high_similarity_threshold=search_request.high_similarity_threshold,
            medium_similarity_threshold=search_request.medium_similarity_threshold,
            model_review_enabled=search_request.model_review_enabled,
            model_settings=model_config,
            enabled=False,
            created_at=now,
            updated_at=now,
        )
        self._ensure_case_index(agent)
        title = search_request.title.strip()
        description = search_request.description.strip()
        issue = ParsedBug(
            bug_id=search_request.issue_key.strip() or "MANUAL-QUERY",
            title=title or self._guess_issue_title(description),
            service=search_request.domain.strip(),
            module=search_request.module.strip(),
            category=search_request.category.strip(),
            status=search_request.status.strip() or "待分析",
            raw_excerpt=description,
        )
        return JiraSolutionSearchResponse(
            result=self._match_current_issue(agent, issue),
            indexed_count=self.store.case_count(),
            embedding_backend=self.store.indexed_embedding_backend(),
            source_db_path=agent.source_db_path,
        )

    def draft_solution_reply(self, draft_request: JiraSolutionDraftReplyRequest) -> JiraSolutionDraftReplyResponse:
        model_config = self.llm_service.resolve_model_config(draft_request.model_settings)
        high_candidates = self._high_confidence_draft_candidates(draft_request.candidates)
        if draft_request.result.match_level != "high" or not high_candidates:
            raise HTTPException(status_code=400, detail="当前没有可用于生成回复草稿的建议复用候选。")

        if model_config.mode != "provider":
            return JiraSolutionDraftReplyResponse(
                draft_text=self._build_learning_draft_reply(
                    description=draft_request.description,
                    result=draft_request.result,
                    candidates=high_candidates,
                ),
                generated_by_model=False,
                model_label="Learning Mode",
                message="当前为 Learning Mode，已生成本地模板草稿；切换到真实模型后会调用大模型汇总。",
            )

        response = self.llm_service.generate_response(
            query="请基于当前问题和既有处理经验，生成一段可直接发给客户的中文回复草稿。",
            messages=[],
            tool_outputs={
                "当前问题": {
                    "问题描述": draft_request.description,
                },
                "参考处理经验": [
                    {
                        "问题描述": candidate.summary,
                        "处理方式": candidate.solution,
                    }
                    for candidate in high_candidates
                ],
            },
            citations=[],
            retrieval_context="",
            model_config=model_config,
            system_prompt=(
                "你是企业客户支持客服。"
                "请只根据给定的问题描述和处理经验，生成一段一次性发给客户的中文回复。"
                "回复语气要温和、明确、可执行，直接告诉客户该怎么做、我们这边会怎么协助。"
                "正文不要出现建议复用、相似度、历史工单号、内部模型说明、问题指纹、命中原因等内部信息。"
                "这是一次性回复场景，不要要求客户补充环境、版本、截图、浏览器、包名等新信息。"
                "不要出现请确认、请提供、发给我们后、补充截图、补充版本、补充浏览器等追问式话术。"
                "如果问题较明确，直接给出处理建议；如果信息不足，也只能基于现有内容给出当前最优处理建议。"
                "结尾最多保留一句“如处理后仍未恢复，可联系支持继续协助”，但不要继续索要材料。"
                "控制在 3 段以内，不要输出 Markdown 表格。"
            ),
        )
        return JiraSolutionDraftReplyResponse(
            draft_text=self._sanitize_customer_reply(response.answer),
            generated_by_model=True,
            model_label=f"{model_config.provider}/{model_config.model}",
            message="已使用当前模型生成回复草稿。",
        )

    def _reindex_cases_for_agent(self, agent: JiraDuplicateAgentConfig) -> dict[str, Any]:
        return self._reindex_cases(source_db_path=agent.source_db_path, agent_id=agent.id)

    def _reindex_cases(self, *, source_db_path: str, agent_id: str) -> dict[str, Any]:
        cases = self._load_completed_cases(source_db_path)
        self._reset_vector_collection()
        documents = [case["search_text"] for case in cases]
        batch_size = 128
        for start in range(0, len(cases), batch_size):
            batch_cases = cases[start : start + batch_size]
            batch_documents = documents[start : start + batch_size]
            embeddings = self.embedding_service.embed_documents(batch_documents)
            if len(embeddings) != len(batch_cases):
                continue
            self.vector_collection.upsert(
                ids=[case["issue_key"] for case in batch_cases],
                documents=batch_documents,
                metadatas=[
                    {
                        "issue_key": case["issue_key"],
                        "summary": case["summary"],
                        "domain": case["domain"],
                        "module": case["module"],
                        "status": case["status"],
                    }
                    for case in batch_cases
                ],
                embeddings=embeddings,
            )
        embedding_backend = self.embedding_service.model_name
        self.store.replace_case_index(
            cases,
            embedding_backend=embedding_backend,
            normalizer_version=MATCH_TEXT_NORMALIZER_VERSION,
            source_db_path=str(Path(source_db_path).expanduser()),
        )
        return {
            "agent_id": agent_id,
            "indexed_count": len(cases),
            "embedding_backend": embedding_backend,
        }

    def _ensure_case_index(self, agent: JiraDuplicateAgentConfig) -> None:
        indexed_source_db_path = self.store.indexed_source_db_path()
        current_source_db_path = str(Path(agent.source_db_path).expanduser())
        if (
            self.store.case_count() <= 0
            or self.store.indexed_normalizer_version() != MATCH_TEXT_NORMALIZER_VERSION
            or (indexed_source_db_path != "" and indexed_source_db_path != current_source_db_path)
        ):
            self._reindex_cases_for_agent(agent)

    def _guess_issue_title(self, description: str) -> str:
        normalized = " ".join((description or "").strip().split())
        if normalized == "":
            return "手工粘贴问题"
        first_sentence = re.split(r"[。！？!?\\n]", normalized, maxsplit=1)[0].strip()
        return (first_sentence or normalized)[:180]

    def _high_confidence_draft_candidates(
        self,
        candidates: list[JiraDuplicateCandidate],
    ) -> list[JiraDuplicateCandidate]:
        return sorted(
            [candidate for candidate in candidates if candidate.score >= DEFAULT_HIGH_SIMILARITY_THRESHOLD],
            key=lambda item: item.score,
            reverse=True,
        )

    def _build_learning_draft_reply(
        self,
        *,
        description: str,
        result: JiraDuplicateIssueResult,
        candidates: list[JiraDuplicateCandidate],
    ) -> str:
        solution_lines = [
            f"- {candidate.solution.strip() or '请先按当前环境配置与账号信息做基础核对。'}"
            for candidate in candidates[:3]
        ]
        return (
            "您好，这个问题初步判断与现有环境配置或版本处理方式有关。\n\n"
            "建议您先按以下方式处理：\n"
            + "\n".join(solution_lines)
            + "\n\n"
            "如按上述方式处理后仍未恢复，可联系支持继续协助处理。"
        )

    def _sanitize_customer_reply(self, text: str) -> str:
        normalized = str(text or "").strip()
        replacements = (
            (r"\[citations?\]", ""),
            (r"\bcited\b", ""),
            (r"建议复用", ""),
            (r"相似度", ""),
            (r"历史工单号?", ""),
            (r"问题指纹", ""),
            (r"命中原因", ""),
            (r"请确认[^。！？!\n]*[。！？!]", "建议您按当前回复内容直接处理。"),
            (r"请提供[^。！？!\n]*[。！？!]", "建议您按当前回复内容直接处理。"),
            (r"发给我们后[^。！？!\n]*[。！？!]", "如处理后仍未恢复，可联系支持继续协助。"),
            (r"补充截图[^。！？!\n]*[。！？!]", "如处理后仍未恢复，可联系支持继续协助。"),
            (r"补充[^。！？!\n]*(版本|浏览器|包名)[^。！？!\n]*[。！？!]", "如处理后仍未恢复，可联系支持继续协助。"),
        )
        for pattern, replacement in replacements:
            normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized.strip()

    def _build_request_headers(self, extra_headers: dict[str, str], *, default_accept: str = "*/*") -> dict[str, str]:
        headers = {key: value for key, value in extra_headers.items() if key.strip() != ""}
        if not any(key.lower() == "accept" for key in headers):
            headers["Accept"] = default_accept
        return headers

    def _count_dashboard_items(self, payload: Any) -> int:
        if isinstance(payload, list):
            return len(payload)
        if isinstance(payload, dict):
            for key in ("records", "items", "bugs", "issues", "list", "results", "table"):
                value = payload.get(key)
                if isinstance(value, list):
                    return len(value)
            for key in ("data", "page", "result", "issueTable"):
                value = payload.get(key)
                if isinstance(value, (dict, list)):
                    nested_count = self._count_dashboard_items(value)
                    if nested_count > 0:
                        return nested_count
            return len(payload)
        return 1

    def _execute_http_request(
        self,
        *,
        target_url: str,
        request_method: str,
        request_headers: dict[str, str],
        request_body_json: dict[str, Any] | None,
        request_body_text: str | None,
        default_accept: str = "*/*",
    ) -> dict[str, Any]:
        headers = self._build_request_headers(request_headers, default_accept=default_accept)
        request_data: bytes | None = None
        if request_method == "POST":
            if request_body_text is not None and request_body_text.strip() != "":
                request_data = request_body_text.encode("utf-8")
            elif request_body_json is not None:
                request_data = json.dumps(request_body_json, ensure_ascii=False).encode("utf-8")
            header_names = {key.lower() for key in headers}
            if "content-type" not in header_names and request_body_json is not None:
                headers["Content-Type"] = "application/json;charset=UTF-8"

        req = request.Request(target_url, data=request_data, headers=headers, method=request_method)
        status_code = 200
        content_type = ""
        raw = ""
        ok = True
        message = "请求成功。"
        for attempt in range(1, 3):
            try:
                with request.urlopen(req, timeout=45) as response:
                    status_code = getattr(response, "status", 200)
                    content_type = response.headers.get("Content-Type", "")
                    raw = response.read().decode("utf-8", errors="replace")
                    ok = True
                    message = "请求成功。"
                    break
            except urlerror.HTTPError as exc:
                ok = False
                status_code = exc.code
                content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
                raw = exc.read().decode("utf-8", errors="replace")
                message = f"接口返回 HTTP {exc.code}"
                break
            except Exception as exc:
                ok = False
                message = str(exc)
                should_retry = attempt < 2 and (
                    "timed out" in message.lower()
                    or "handshake" in message.lower()
                    or isinstance(exc, TimeoutError)
                )
                if not should_retry:
                    break
                time.sleep(1.0)
        return {
            "ok": ok,
            "status_code": status_code,
            "message": message,
            "content_type": content_type,
            "raw": raw,
        }

    def _execute_dashboard_request(
        self,
        *,
        dashboard_url: str,
        request_method: str,
        request_headers: dict[str, str],
        request_body_json: dict[str, Any] | None,
        request_body_text: str | None,
    ) -> dict[str, Any]:
        result = self._execute_http_request(
            target_url=dashboard_url,
            request_method=request_method,
            request_headers=request_headers,
            request_body_json=request_body_json,
            request_body_text=request_body_text,
            default_accept="application/json",
        )
        raw = str(result["raw"])
        parsed_payload: Any = None
        parsed_item_count = 0
        if raw.strip() != "":
            try:
                parsed_payload = json.loads(raw)
                parsed_item_count = self._count_dashboard_items(parsed_payload)
            except Exception:
                parsed_payload = None
        preview = raw[:12000]
        if len(raw) > 12000:
            preview += "\n...<truncated>"
        return {
            **result,
            "parsed_payload": parsed_payload,
            "parsed_item_count": parsed_item_count,
            "response_body_preview": preview,
        }

    def _fetch_current_jira_issues(self, agent: JiraDuplicateAgentConfig) -> tuple[Any, int]:
        result = self._execute_dashboard_request(
            dashboard_url=agent.dashboard_url,
            request_method=agent.request_method,
            request_headers=agent.request_headers,
            request_body_json=agent.request_body_json,
            request_body_text=agent.request_body_text,
        )
        if not result["ok"] and result["response_body_preview"] == "":
            raise RuntimeError(result["message"])
        if result["parsed_payload"] is None:
            raise RuntimeError("接口没有返回可解析的 JSON。")
        return result["parsed_payload"], int(result["parsed_item_count"])

    def _detail_request_enabled(self, agent: JiraDuplicateAgentConfig) -> bool:
        return agent.detail_url_template is not None and agent.detail_url_template.strip() != ""

    def _get_header_case_insensitive(self, headers: dict[str, str], name: str) -> str:
        target = name.strip().lower()
        for key, value in headers.items():
            if key.strip().lower() == target:
                return value
        return ""

    def _set_header_case_insensitive(self, headers: dict[str, str], name: str, value: str) -> dict[str, str]:
        updated = {key: item for key, item in headers.items() if key.strip().lower() != name.strip().lower()}
        updated[name] = value
        return updated

    def _execute_detail_request_with_cookie_fallback(
        self,
        *,
        agent: JiraDuplicateAgentConfig,
        target_url: str,
        request_headers: dict[str, str],
        request_body_text: str | None,
    ) -> dict[str, Any]:
        result = self._execute_http_request(
            target_url=target_url,
            request_method=agent.detail_request_method,
            request_headers=request_headers,
            request_body_json=None,
            request_body_text=request_body_text,
            default_accept="*/*",
        )
        if int(result["status_code"]) not in {401, 403}:
            return result
        current_cookie = self._get_header_case_insensitive(request_headers, "Cookie").strip()
        list_cookie = self._get_header_case_insensitive(agent.request_headers, "Cookie").strip()
        if list_cookie == "" or current_cookie == list_cookie:
            return result
        return self._execute_http_request(
            target_url=target_url,
            request_method=agent.detail_request_method,
            request_headers=self._set_header_case_insensitive(request_headers, "Cookie", list_cookie),
            request_body_json=None,
            request_body_text=request_body_text,
            default_accept="*/*",
        )

    def _render_issue_template(self, template: str, issue: ParsedBug) -> str:
        timestamp_ms = str(int(time.time() * 1000))
        replacements = {
            "{{issue_key}}": issue.bug_id,
            "{{issueKey}}": issue.bug_id,
            "{{bug_id}}": issue.bug_id,
            "{{jira_issue_id}}": issue.jira_issue_id,
            "{{issue_id}}": issue.jira_issue_id,
            "{{aid}}": issue.bug_aid,
            "{{timestamp_ms}}": timestamp_ms,
            "{{now_ms}}": timestamp_ms,
        }
        rendered = template
        for needle, value in replacements.items():
            rendered = rendered.replace(needle, value or "")
        return rendered

    def _hydrate_issue_details(self, agent: JiraDuplicateAgentConfig, issues: list[ParsedBug]) -> list[ParsedBug]:
        if not self._detail_request_enabled(agent) or not issues:
            return issues

        hydrated: list[ParsedBug] = []
        for issue in issues:
            rendered_url = self._render_issue_template(agent.detail_url_template or "", issue).strip()
            if rendered_url == "":
                hydrated.append(issue)
                continue
            headers = dict(agent.request_headers)
            headers.update(agent.detail_request_headers)
            rendered_headers = {
                key: self._render_issue_template(str(value), issue)
                for key, value in headers.items()
                if key.strip() != ""
            }
            rendered_body = (
                self._render_issue_template(agent.detail_request_body_text, issue)
                if agent.detail_request_body_text is not None
                else None
            )
            try:
                detail_result = self._execute_detail_request_with_cookie_fallback(
                    agent=agent,
                    target_url=rendered_url,
                    request_headers=rendered_headers,
                    request_body_text=rendered_body,
                )
            except Exception:
                hydrated.append(issue)
                continue
            detail_raw = str(detail_result["raw"] or "")
            detail_payload = self._load_json_if_possible(detail_raw)
            next_issue = self._merge_detail_payload(issue, detail_payload, detail_raw)
            hydrated.append(next_issue)
        return hydrated

    def _load_json_if_possible(self, raw: str) -> Any | None:
        try:
            return json.loads(raw)
        except Exception:
            return None

    def _strip_html_to_text(self, raw: str) -> str:
        normalized = html.unescape(raw or "")
        normalized = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", normalized)
        normalized = re.sub(r"(?i)<br\s*/?>", "\n", normalized)
        normalized = re.sub(r"(?i)</?(div|p|li|tr|td|th|dd|dt|section|article|h[1-6])[^>]*>", "\n", normalized)
        normalized = re.sub(r"(?s)<[^>]+>", " ", normalized)
        lines = [re.sub(r"\s+", " ", line).strip(" \t\r\n:：-") for line in normalized.splitlines()]
        return "\n".join(line for line in lines if line != "").strip()

    def _merge_detail_payload(self, issue: ParsedBug, payload: Any | None, raw: str) -> ParsedBug:
        title = issue.title
        description = ""
        status = issue.status
        service = issue.service
        module = issue.module

        if isinstance(payload, dict):
            fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
            title = self._stringify(fields.get("summary")) or self._stringify(payload.get("summary")) or title
            description = self._stringify(fields.get("description")) or self._stringify(payload.get("description"))
            status = (
                self._stringify((fields.get("status") or {}).get("name"))
                if isinstance(fields.get("status"), dict)
                else self._stringify(fields.get("status"))
            ) or self._stringify(payload.get("status")) or status
            components = fields.get("components")
            if isinstance(components, list) and components:
                module = " / ".join(self._stringify(item.get("name")) for item in components if isinstance(item, dict)).strip(" /") or module
            panel_fields = self._extract_jira_panel_fields(payload)
            service = self._get_panel_field(panel_fields, "领域", "初始领域") or service
            module = self._get_panel_field(panel_fields, "模块", "初始模块") or module
            category = self._get_panel_field(panel_fields, "领域模块")
            if category and (not service or not module):
                service, module = self._split_service_module(category)

        detail_text = description or self._strip_html_to_text(raw)
        excerpt_parts = [issue.raw_excerpt.strip()] if issue.raw_excerpt.strip() else []
        if detail_text.strip():
            excerpt_parts.append("[详情接口]\n" + detail_text.strip())
        return issue.model_copy(
            update={
                "title": title,
                "service": service,
                "module": module,
                "category": " - ".join(part for part in (service, module) if part),
                "status": status,
                "raw_excerpt": "\n\n".join(excerpt_parts)[:3000],
            }
        )

    def _stringify(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float, bool)):
            return str(value).strip()
        if isinstance(value, list):
            return " / ".join(part for part in (self._stringify(item) for item in value) if part)
        if isinstance(value, dict):
            for key in ("text", "name", "title", "value", "label", "displayName"):
                text = self._stringify(value.get(key))
                if text:
                    return text
            return json.dumps(value, ensure_ascii=False)[:800]
        return str(value).strip()

    def _normalize_panel_label(self, value: str) -> str:
        return re.sub(r"\s+", "", value.strip().strip(":："))

    def _extract_jira_panel_fields(self, payload: Any) -> dict[str, str]:
        if not isinstance(payload, dict):
            return {}
        panels = payload.get("panels")
        if not isinstance(panels, dict):
            return {}
        fields: dict[str, str] = {}
        for key in ("leftPanels", "rightPanels", "infoPanels"):
            items = panels.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("html"), str):
                    continue
                panel_html = item["html"]
                for matched in re.finditer(r"(?is)<dt\b[^>]*>(.*?)</dt>\s*<dd\b[^>]*>(.*?)</dd>", panel_html):
                    label = self._normalize_panel_label(self._strip_html_to_text(matched.group(1)))
                    value = self._strip_html_to_text(matched.group(2)).strip()
                    if label and value and label not in fields:
                        fields[label] = value
        return fields

    def _get_panel_field(self, fields: dict[str, str], *labels: str) -> str:
        for label in labels:
            normalized = self._normalize_panel_label(label)
            if fields.get(normalized, "").strip():
                return fields[normalized].strip()
        return ""

    def _split_service_module(self, value: str) -> tuple[str, str]:
        normalized = re.sub(r"\s+", " ", value).strip().strip("-")
        if normalized == "":
            return "", ""
        for separator in (" - ", " / ", " | ", "-", "/", "|", ">", " > "):
            if separator in normalized:
                left, right = normalized.split(separator, 1)
                if left.strip() and right.strip():
                    return left.strip(), right.strip()
        return normalized, ""

    def _load_completed_cases(self, source_db_path: str) -> list[dict[str, str]]:
        path = Path(source_db_path).expanduser()
        if not path.exists():
            raise RuntimeError(f"Jira 源数据库不存在：{path}")
        uri = f"file:{path}?mode=ro"
        placeholders = ",".join("?" for _ in COMPLETED_STATUSES)
        sql = f"""
            SELECT issue_key, summary, description, domain, module, status, solution,
                   versions, fix_versions, updated
            FROM jira_support_issues
            WHERE COALESCE(TRIM(issue_key), '') <> ''
              AND COALESCE(TRIM(summary), '') <> ''
              AND COALESCE(TRIM(solution), '') <> ''
              AND status IN ({placeholders})
            ORDER BY updated DESC
        """
        with sqlite3.connect(uri, uri=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, COMPLETED_STATUSES).fetchall()
        cases: list[dict[str, str]] = []
        for row in rows:
            case = {key: str(row[key] or "").strip() for key in row.keys()}
            case["search_text"] = self._compose_case_search_text(case)
            cases.append(case)
        return cases

    def _compose_case_search_text(self, case: dict[str, str]) -> str:
        parts = [
            case.get("summary", ""),
            case.get("description", ""),
            f"领域：{case.get('domain', '')}",
            f"模块：{case.get('module', '')}",
            f"版本：{case.get('versions', '')}",
            f"修复版本：{case.get('fix_versions', '')}",
        ]
        return self._clean_match_text("\n".join(part for part in parts if part.strip()))

    def _compose_issue_search_text(self, issue: ParsedBug) -> str:
        relevant_excerpt = self._relevant_issue_excerpt(issue.raw_excerpt)
        parts = [
            issue.title,
            f"领域：{issue.service}",
            f"模块：{issue.module}",
            f"分类：{issue.category}",
            f"状态：{issue.status}",
            relevant_excerpt,
        ]
        return self._clean_match_text("\n".join(part for part in parts if part.strip()))

    def _clean_match_text(self, text: str) -> str:
        normalized = html.unescape(text or "").replace("\r\n", "\n").replace("\r", "\n")
        cleaned_lines: list[str] = []
        for raw_line in normalized.splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            if line == "":
                continue
            line = re.sub(r"https?://\S*shareLogin/\S+", " ", line, flags=re.IGNORECASE)
            line = re.sub(r"https?://\S*cas/share\S+", " ", line, flags=re.IGNORECASE)
            line = re.sub(r"[【\[]\s*(?:帐户|账户|账号|帐号)\s*分享链接\s*[】\]].*$", " ", line)
            line = re.sub(r"(?:帐户|账户|账号|帐号)\s*分享链接\s*[:：]?.*$", " ", line)
            line = re.sub(r"(?:[【\[]\s*)?dsp\s*支持问题\s*(?:[】\]]\s*)?", " ", line, flags=re.IGNORECASE)
            line = re.sub(r"\s+", " ", line).strip()
            if line == "":
                continue
            if re.fullmatch(r"[-_=—–~*·.。\s]{6,}", line):
                continue
            cleaned_lines.append(line)
        return "\n".join(cleaned_lines).strip()

    def _relevant_issue_excerpt(self, raw_excerpt: str) -> str:
        text = self._strip_html_to_text(raw_excerpt)
        if text.strip() == "":
            return ""
        useful_markers = (
            "dsp",
            "支持问题",
            "ca",
            "登录",
            "登陆",
            "补丁",
            "合集",
            "弹框",
            "弹窗",
            "加载",
            "刷新",
            "驱动",
            "报错",
            "许可",
            "工作台",
        )
        lines: list[str] = []
        for raw_line in text.splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            if line == "" or len(line) > 420:
                continue
            punctuation_count = sum(line.count(item) for item in ('"', "{", "}", "[", "]", ":", ","))
            if punctuation_count >= 6:
                continue
            lowered = line.lower()
            if any(marker in lowered for marker in useful_markers):
                lines.append(line)
            if len("\n".join(lines)) >= 800:
                break
        return "\n".join(lines)[:900]

    def _query_vector_candidates(self, query: str, *, limit: int = 40) -> list[tuple[float, str]]:
        if self.store.case_count() <= 0 or query.strip() == "":
            return []
        try:
            embedding = self.embedding_service.embed_query(query)
            indexed_backend = self.store.indexed_embedding_backend()
            if indexed_backend and indexed_backend != self.embedding_service.model_name:
                return []
            result = self.vector_collection.query(
                query_embeddings=[embedding],
                n_results=min(limit, self.store.case_count()),
                include=["metadatas", "distances"],
            )
        except Exception:
            return []
        ids = result.get("ids", [[]])[0]
        distances = result.get("distances", [[]])[0]
        hits: list[tuple[float, str]] = []
        for issue_key, distance in zip(ids, distances):
            score = max(0.0, 1.0 - float(distance if distance is not None else 1.0))
            hits.append((score, str(issue_key)))
        return hits

    def _match_current_issue(self, agent: JiraDuplicateAgentConfig, issue: ParsedBug) -> JiraDuplicateIssueResult:
        query = self._compose_issue_search_text(issue)
        clean_issue_title = self._clean_match_text(issue.title)
        high_threshold = min(agent.high_similarity_threshold, DEFAULT_HIGH_SIMILARITY_THRESHOLD)
        medium_threshold = min(agent.medium_similarity_threshold, DEFAULT_MEDIUM_SIMILARITY_THRESHOLD)
        score_buckets: dict[str, _ScoreBucket] = defaultdict(_ScoreBucket)

        fts_hits = self.store.fts_search(query, limit=40)
        for rank, (score, case) in enumerate(fts_hits, start=1):
            bucket = score_buckets[case["issue_key"]]
            bucket.lexical_rank = rank
            bucket.lexical_score = max(bucket.lexical_score, score)

        vector_hits = self._query_vector_candidates(query, limit=40)
        for rank, (score, issue_key) in enumerate(vector_hits, start=1):
            bucket = score_buckets[issue_key]
            bucket.vector_rank = rank
            bucket.vector_score = max(bucket.vector_score, score)

        manual_candidates = self._manual_candidate_scores(query, clean_issue_title, limit=40)
        for rank, (score, case) in enumerate(manual_candidates, start=1):
            bucket = score_buckets[case["issue_key"]]
            if bucket.lexical_rank is None:
                bucket.lexical_rank = rank + 40
            bucket.lexical_score = max(bucket.lexical_score, score)

        cases_by_key = self.store.get_index_cases(list(score_buckets.keys()))
        ranked: list[tuple[float, str, str]] = []
        query_signature = self._issue_signature(query, domain=issue.service, module=issue.module)
        for issue_key, bucket in score_buckets.items():
            case = cases_by_key.get(issue_key)
            if case is None:
                continue
            clean_case_summary = self._clean_match_text(case.get("summary", ""))
            title_score = self._case_similarity(clean_issue_title, clean_case_summary)
            full_score = self._case_similarity(query, case.get("search_text", ""))
            manual_score = max(title_score, full_score * 0.95, bucket.lexical_score)
            vector_score = bucket.vector_score
            rrf = 0.0
            if bucket.lexical_rank is not None:
                rrf += 1.0 / (60 + bucket.lexical_rank)
            if bucket.vector_rank is not None:
                rrf += 1.0 / (60 + bucket.vector_rank)
            bonus = 0.0
            if issue.service and issue.service == case.get("domain", ""):
                bonus += 0.03
            if issue.module and issue.module == case.get("module", ""):
                bonus += 0.03
            if bucket.lexical_rank is not None and bucket.lexical_rank <= 10:
                bonus += 0.02
            keyword_bonus = self._important_overlap_bonus(query, clean_case_summary)
            bonus += keyword_bonus
            base_score = min(1.0, max(manual_score, vector_score * 0.96) + min(0.08, rrf * 2.5) + bonus)
            candidate_signature = self._issue_signature(
                case.get("search_text", ""),
                domain=case.get("domain", ""),
                module=case.get("module", ""),
            )
            signature_score, signature_reason, should_promote = self._signature_match(
                query_signature,
                candidate_signature,
                base_score=base_score,
            )
            final_score = min(1.0, base_score + min(0.12, signature_score * 0.12))
            if should_promote:
                final_score = max(final_score, 0.82)
            elif agent.model_review_enabled and medium_threshold <= final_score < high_threshold:
                model_score, model_reason = self._model_boundary_judgement(
                    agent=agent,
                    issue=issue,
                    case=case,
                    query_signature=query_signature,
                    candidate_signature=candidate_signature,
                )
                if model_score >= 0.78:
                    final_score = max(final_score, 0.80)
                    if model_reason:
                        signature_reason = "；".join(part for part in (signature_reason, model_reason) if part)
            reason_parts = [
                f"标题相似 {title_score:.2f}",
                f"全文相似 {full_score:.2f}",
            ]
            if vector_score > 0:
                reason_parts.append(f"向量相似 {vector_score:.2f}")
            if bonus > 0:
                reason_parts.append("领域/模块或关键词加权")
            if signature_reason:
                reason_parts.append(signature_reason)
            ranked.append((final_score, issue_key, "；".join(reason_parts)))

        ranked.sort(key=lambda item: item[0], reverse=True)
        visible = [(score, key, reason) for score, key, reason in ranked if score >= medium_threshold][:3]
        candidates: list[JiraDuplicateCandidate] = []
        for score, issue_key, reason in visible:
            case = cases_by_key[issue_key]
            candidates.append(
                JiraDuplicateCandidate(
                    issue_key=issue_key,
                    summary=case.get("summary", ""),
                    domain=case.get("domain", ""),
                    module=case.get("module", ""),
                    status=case.get("status", ""),
                    solution=case.get("solution", ""),
                    score=round(score, 4),
                    reason=reason,
                )
            )

        top_score = candidates[0].score if candidates else (ranked[0][0] if ranked else 0.0)
        if candidates and top_score >= high_threshold:
            match_level = "high"
            match_reason = "命中高相似历史已完成工单，建议复用候选解决方案。"
        elif candidates and top_score >= medium_threshold:
            match_level = "medium"
            match_reason = "命中中等相似历史已完成工单，建议人工确认后复用。"
        elif top_score > 0:
            match_level = "low"
            match_reason = "存在弱相似历史工单，但未达到展示解决方案阈值。"
        else:
            match_level = "none"
            match_reason = "未找到相似历史已完成工单。"

        return JiraDuplicateIssueResult(
            issue_key=issue.bug_id,
            jira_issue_id=issue.jira_issue_id,
            title=issue.title,
            description=issue.raw_excerpt,
            domain=issue.service,
            module=issue.module,
            status=issue.status,
            raw_excerpt=issue.raw_excerpt,
            match_level=match_level,
            match_score=round(top_score, 4),
            match_reason=match_reason,
            candidates=candidates,
        )

    def _manual_candidate_scores(
        self,
        query: str,
        clean_title: str,
        *,
        limit: int,
    ) -> list[tuple[float, dict[str, str]]]:
        scored: list[tuple[float, dict[str, str]]] = []
        query_signature = self._issue_signature(query)
        for case in self.store.list_index_cases():
            clean_case_summary = self._clean_match_text(case.get("summary", ""))
            score = max(
                self._case_similarity(clean_title, clean_case_summary),
                self._case_similarity(query, case.get("search_text", "")) * 0.95,
            )
            signature_score, _reason, should_promote = self._signature_match(
                query_signature,
                self._issue_signature(
                    case.get("search_text", ""),
                    domain=case.get("domain", ""),
                    module=case.get("module", ""),
                ),
                base_score=score,
            )
            if score >= 0.35 or (should_promote and score >= 0.20) or (signature_score >= 0.5 and score >= 0.28):
                signature_boost = signature_score * (0.62 if should_promote else 0.72)
                scored.append((max(score, signature_boost), case))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[:limit]

    def _normalize_similarity_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().lower()

    def _pattern_labels(self, text: str, patterns: dict[str, tuple[str, ...]]) -> set[str]:
        labels: set[str] = set()
        for label, label_patterns in patterns.items():
            if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in label_patterns):
                labels.add(label)
        return labels

    def _issue_signature(self, text: str, *, domain: str = "", module: str = "") -> _IssueSignature:
        normalized = self._normalize_similarity_text("\n".join(part for part in (text, domain, module) if part))
        object_patterns = {
            "CA": (r"(?<![a-z0-9])ca(?![a-z0-9])", r"u\s*key", r"ukey", r"数字证书", r"证书", r"介质"),
            "用户": (r"用户", r"账号", r"账户", r"人员", r"员工", r"出纳", r"user", r"account"),
            "报表": (r"报表", r"表单", r"看板", r"取数"),
            "许可": (r"许可", r"授权", r"license"),
            "接口": (r"接口", r"\bapi\b", r"url", r"请求", r"服务地址"),
            "菜单入口": (r"菜单", r"入口", r"按钮", r"导航"),
            "页面": (r"页面", r"弹框", r"弹窗", r"窗口"),
            "单据": (r"单据", r"凭证", r"订单", r"发票", r"合同"),
            "流程": (r"流程", r"审批流", r"审批"),
            "组织": (r"组织", r"部门", r"集团", r"公司", r"租户"),
            "数据": (r"数据", r"字段", r"记录", r"档案"),
            "工作台": (r"工作台",),
        }
        action_patterns = {
            "登录": (r"登录", r"登陆", r"登入", r"登不上", r"无法登"),
            "导出": (r"导出", r"下载", r"导excel", r"导出excel"),
            "导入": (r"导入", r"上传"),
            "保存": (r"保存", r"暂存"),
            "提交": (r"提交", r"送审", r"上报"),
            "同步": (r"同步", r"推送", r"拉取"),
            "打开": (r"打开", r"访问", r"进入", r"点击", r"点开"),
            "查询": (r"查询", r"搜索", r"检索", r"过滤"),
            "审批": (r"审批", r"审核"),
            "打印": (r"打印",),
            "刷新": (r"刷新", r"refresh"),
            "切换": (r"切换", r"变更"),
            "新增": (r"新增", r"创建"),
            "删除": (r"删除", r"移除"),
            "修改": (r"修改", r"编辑", r"调整"),
            "计算": (r"计算", r"重算"),
        }
        symptom_patterns = {
            "登录失败": (r"登不上", r"无法登录", r"无法登陆", r"登录失败", r"登陆失败", r"不能登录", r"不能登陆"),
            "加载异常": (r"加载不出来", r"加载不出", r"一直加载", r"加载失败", r"加载异常", r"转圈"),
            "界面异常": (
                r"弹框",
                r"弹窗",
                r"页面刷新",
                r"页面.{0,10}刷新",
                r"刷新.{0,10}页面",
                r"空白页",
                r"白屏",
                r"页面空白",
                r"不显示",
                r"显示异常",
                r"弹不出来",
            ),
            "报错": (r"报错", r"错误", r"异常", r"\berror\b", r"exception"),
            "超时": (r"超时", r"timeout", r"响应慢"),
            "权限异常": (r"无权限", r"没有权限", r"权限不足", r"未授权", r"授权没了", r"许可没了"),
            "数据异常": (r"数据不对", r"数据错误", r"不一致", r"丢失", r"缺失", r"重复", r"为空"),
            "服务异常": (r"服务重启", r"服务挂", r"宕机", r"崩溃", r"重启"),
            "失败": (r"失败", r"不成功", r"不能", r"无法"),
            "卡顿": (r"卡顿", r"很慢", r"慢"),
            "不生效": (r"不生效", r"没生效", r"无效"),
        }
        environment_patterns = {
            "补丁合集": (r"补丁", r"合集", r"\bpatch\b"),
            "升级": (r"升级", r"升版", r"更新"),
            "版本": (r"版本", r"版号", r"\bv\d+(?:\.\d+)*\b"),
            "驱动": (r"驱动", r"\bdriver\b"),
            "浏览器": (r"浏览器", r"\bchrome\b", r"\bedge\b", r"\bie\b"),
            "内外网": (r"内网", r"外网", r"网络"),
            "私有云": (r"私有云", r"专属云"),
            "环境切换": (r"环境切换", r"切环境", r"测试环境", r"生产环境"),
            "数据库": (r"数据库", r"\bsql\b", r"oracle", r"mysql"),
            "移动端": (r"移动端", r"手机端", r"app端", r"\bapp\b"),
            "客户端": (r"客户端", r"pc端"),
        }
        clues: set[str] = set()
        for matched in re.findall(r"(?:error|err|错误码|异常码|报错)[：:\s-]*([a-zA-Z0-9_-]{3,})", normalized):
            clues.add(f"错误码:{matched.lower()}")
        for matched in re.findall(r"\b(?:[A-Z]{2,}-\d{2,}|QP\d{6,}-\d+)\b", text, flags=re.IGNORECASE):
            clues.add(f"编号:{matched.lower()}")
        for matched in re.findall(r"(?<!\d)(\d{3})(?=\s*(?:补丁|合集|版本|patch))", normalized):
            clues.add(f"版本号:{matched}")

        domains = {
            item.strip()
            for item in (domain, module)
            if item and item.strip() and item.strip() not in {"-", "无", "未知"}
        }
        return _IssueSignature(
            objects=frozenset(self._pattern_labels(normalized, object_patterns)),
            actions=frozenset(self._pattern_labels(normalized, action_patterns)),
            symptoms=frozenset(self._pattern_labels(normalized, symptom_patterns)),
            environments=frozenset(self._pattern_labels(normalized, environment_patterns)),
            clues=frozenset(clues),
            domains=frozenset(domains),
        )

    def _signature_match(
        self,
        query_signature: _IssueSignature,
        candidate_signature: _IssueSignature,
        *,
        base_score: float,
    ) -> tuple[float, str, bool]:
        category_data = [
            ("对象", query_signature.objects, candidate_signature.objects, 0.30, {"用户", "数据", "页面", "工作台"}),
            ("操作", query_signature.actions, candidate_signature.actions, 0.24, {"打开", "刷新", "修改"}),
            ("现象", query_signature.symptoms, candidate_signature.symptoms, 0.24, {"失败"}),
            ("环境", query_signature.environments, candidate_signature.environments, 0.16, {"版本"}),
            ("线索", query_signature.clues, candidate_signature.clues, 0.20, set()),
            ("领域模块", query_signature.domains, candidate_signature.domains, 0.06, {"工作台"}),
        ]
        score = 0.0
        reason_parts: list[str] = []
        strong_categories: set[str] = set()
        strong_overlaps: dict[str, set[str]] = {}
        for label, query_values, candidate_values, weight, weak_values in category_data:
            overlap = set(query_values) & set(candidate_values)
            if not overlap:
                continue
            visible_overlap = sorted(overlap)[:4]
            reason_parts.append(f"{label}={('/'.join(visible_overlap))}")
            strong_overlap = overlap - weak_values
            if strong_overlap:
                score += weight
                strong_categories.add(label)
                strong_overlaps[label] = strong_overlap
            else:
                score += weight * 0.35

        strong_query_objects = query_signature.objects - {"用户", "数据", "页面", "工作台"}
        has_required_object = not strong_query_objects or bool(strong_overlaps.get("对象"))
        has_object_action_context = "对象" in strong_categories and "操作" in strong_categories
        has_problem_context = "现象" in strong_categories or "环境" in strong_categories or "线索" in strong_categories
        has_action_problem_context_without_strong_object = (
            not strong_query_objects
            and "操作" in strong_categories
            and "现象" in strong_categories
            and ("环境" in strong_categories or "领域模块" in strong_categories)
        )
        has_high_signature_context = (
            score >= 0.84
            and len(strong_categories) >= 4
            and has_required_object
            and has_object_action_context
            and has_problem_context
        )
        should_promote = has_required_object and (
            (
                base_score >= 0.55
                and (
                    (has_object_action_context and has_problem_context)
                    or ("线索" in strong_categories and len(strong_categories) >= 2)
                    or has_action_problem_context_without_strong_object
                )
            )
            or (base_score >= 0.22 and has_high_signature_context)
        )
        reason = f"问题指纹匹配：{'，'.join(reason_parts)}" if reason_parts else ""
        return min(1.0, score), reason, should_promote

    def _model_boundary_judgement(
        self,
        *,
        agent: JiraDuplicateAgentConfig,
        issue: ParsedBug,
        case: dict[str, str],
        query_signature: _IssueSignature,
        candidate_signature: _IssueSignature,
    ) -> tuple[float, str]:
        if not agent.model_review_enabled or agent.model_settings.mode != "provider":
            return 0.0, ""
        try:
            answer = self.llm_service.generate_response(
                query=(
                    "判断两个 Jira 支持工单是否属于同一类问题，历史工单解决方案是否可供当前工单复用。"
                    "只输出 JSON：{\"same_issue\": true/false, \"confidence\": 0-1, \"reason\": \"...\"}。"
                ),
                messages=[],
                tool_outputs={
                    "当前工单": {
                        "标题": issue.title,
                        "领域": issue.service,
                        "模块": issue.module,
                        "问题指纹": query_signature.__dict__,
                    },
                    "历史工单": {
                        "标题": case.get("summary", ""),
                        "领域": case.get("domain", ""),
                        "模块": case.get("module", ""),
                        "问题指纹": candidate_signature.__dict__,
                        "解决方案": case.get("solution", "")[:600],
                    },
                },
                citations=[],
                retrieval_context="",
                model_config=agent.model_settings,
                system_prompt="你是企业支持工单的重复问题审核员，判断要保守，只有同类根因或处理路径高度一致才返回 same_issue=true。",
            ).answer
        except Exception:
            return 0.0, ""
        matched = re.search(r"\{.*\}", answer, flags=re.DOTALL)
        if matched is None:
            return 0.0, ""
        try:
            payload = json.loads(matched.group(0))
        except Exception:
            return 0.0, ""
        if not isinstance(payload, dict) or payload.get("same_issue") is not True:
            return 0.0, ""
        try:
            confidence = float(payload.get("confidence", 0))
        except Exception:
            confidence = 0.0
        reason = self._stringify(payload.get("reason", "")).strip()
        return max(0.0, min(1.0, confidence)), f"模型复核：{reason}" if reason else "模型复核：同类问题"

    def _feature_terms(self, text: str) -> set[str]:
        normalized = self._normalize_similarity_text(text)
        terms = set(re.findall(r"[a-zA-Z0-9_+#.-]{2,}", normalized))
        chinese_parts = re.findall(r"[\u4e00-\u9fff]+", normalized)
        for part in chinese_parts:
            if len(part) <= 4:
                terms.add(part)
                continue
            for size in (2, 3):
                for index in range(0, len(part) - size + 1):
                    terms.add(part[index : index + size])
        if re.search(r"(?<![a-z0-9])ca(?![a-z0-9])", normalized):
            terms.add("ca")
        if re.search(r"登录|登陆|登入|登不上|无法登录|无法登陆", normalized):
            terms.add("login")
        if re.search(r"补丁|合集|patch", normalized):
            terms.add("patch")
        if re.search(r"弹框|弹窗|弹出|弹不出来|加载不出来", normalized):
            terms.add("popup")
        if re.search(r"加载|load", normalized):
            terms.add("load")
        if re.search(r"刷新|refresh", normalized):
            terms.add("refresh")
        if re.search(r"驱动|driver", normalized):
            terms.add("driver")
        if re.search(r"密码|口令|password", normalized):
            terms.add("password")
        if re.search(r"出纳", normalized):
            terms.add("cashier")
        if re.search(r"用户|账号|账户|user|account", normalized):
            terms.add("user")
        return terms

    def _important_overlap_bonus(self, query: str, candidate: str) -> float:
        stop_terms = {
            "dsp",
            "支持",
            "问题",
            "老师",
            "您好",
            "麻烦",
            "请问",
            "一下",
            "这个",
            "现在",
        }
        query_terms = {term for term in self._feature_terms(query) if term not in stop_terms and len(term) >= 2}
        candidate_terms = {term for term in self._feature_terms(candidate) if term not in stop_terms and len(term) >= 2}
        overlap = query_terms & candidate_terms
        strong_overlap = {
            term
            for term in overlap
            if re.search(r"[a-zA-Z0-9]", term) or term in {"补丁", "合集", "登录", "登陆", "弹框", "驱动", "加载", "刷新"}
        }
        support_overlap = strong_overlap & {"ca", "login", "patch", "popup", "load", "refresh", "driver", "password"}
        if {"ca", "patch"} <= support_overlap and ({"login", "popup", "refresh"} & support_overlap):
            return 0.18
        if len(support_overlap) >= 3:
            return min(0.16, 0.045 * len(support_overlap))
        if len(strong_overlap) >= 2:
            return min(0.12, 0.03 * len(strong_overlap))
        if len(overlap) >= 5:
            return 0.04
        return 0.0

    def _case_similarity(self, query: str, candidate: str) -> float:
        normalized_query = self._normalize_similarity_text(query)
        normalized_candidate = self._normalize_similarity_text(candidate)
        if not normalized_query or not normalized_candidate:
            return 0.0
        import difflib

        sequence_ratio = difflib.SequenceMatcher(a=normalized_query, b=normalized_candidate).ratio()
        query_terms = self._feature_terms(normalized_query)
        candidate_terms = self._feature_terms(normalized_candidate)
        if not query_terms or not candidate_terms:
            return sequence_ratio
        overlap_ratio = len(query_terms & candidate_terms) / len(query_terms)
        jaccard = len(query_terms & candidate_terms) / len(query_terms | candidate_terms)
        return max(sequence_ratio, overlap_ratio * 0.92, jaccard * 1.08)

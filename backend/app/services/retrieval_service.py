"""共享的 scoped retrieval 执行服务。"""

from __future__ import annotations

from typing import Any

from ..rag.pipeline import RAGPipeline
from ..schemas import ModelConfig, RetrievalProfile, RetrievalResult, ScopeType
from .knowledge_store import KnowledgeStore
from .llm_service import LLMService


class RetrievalService:
    """统一产出检索模式和 Agent 复用的检索结果。"""

    def __init__(self, knowledge_store: KnowledgeStore, llm_service: LLMService) -> None:
        self.rag_pipeline = RAGPipeline(knowledge_store, llm_service)
        self.llm_service = llm_service

    def run(
        self,
        *,
        query: str,
        scope_type: ScopeType = "global",
        scope_id: str | None = None,
        model_config: ModelConfig,
        system_prompt: str | None = None,
        retrieval_profile: RetrievalProfile = "default",
        query_bundle_context: dict[str, Any] | None = None,
    ) -> RetrievalResult:
        rag_result = self.rag_pipeline.run(
            query=query,
            scope_type=scope_type,
            scope_id=scope_id,
            model_config=model_config,
            retrieval_profile=retrieval_profile,
            query_bundle_context=query_bundle_context,
        )
        summary = self.llm_service.summarize_retrieval(
            query=query,
            citations=rag_result.citations,
            retrieval_context=rag_result.retrieval_context,
            evidence_cards=rag_result.debug.selected_chunks if rag_result.debug is not None else None,
            model_config=model_config,
            system_prompt=system_prompt,
        ).strip()
        return RetrievalResult(
            query=rag_result.query,
            scope_type=rag_result.scope_type,
            scope_id=rag_result.scope_id,
            citations=rag_result.citations,
            retrieval_context=rag_result.retrieval_context,
            summary=summary,
            related_document_links=self.rag_pipeline.build_related_document_links(rag_result.citations),
            debug=rag_result.debug,
        )

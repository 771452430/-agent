"""共享的 scoped retrieval 执行服务。"""

from __future__ import annotations

from ..rag.pipeline import RAGPipeline
from ..schemas import ModelConfig, RetrievalResult, ScopeType
from .knowledge_store import KnowledgeStore
from .llm_service import LLMService


class RetrievalService:
    """统一产出检索模式和 Agent 复用的检索结果。"""

    def __init__(self, knowledge_store: KnowledgeStore, llm_service: LLMService) -> None:
        self.rag_pipeline = RAGPipeline(knowledge_store)
        self.llm_service = llm_service

    def run(
        self,
        *,
        query: str,
        scope_type: ScopeType = "global",
        scope_id: str | None = None,
        model_config: ModelConfig,
        system_prompt: str | None = None,
    ) -> RetrievalResult:
        rag_result = self.rag_pipeline.run(
            query=query,
            scope_type=scope_type,
            scope_id=scope_id,
        )
        summary = self.llm_service.summarize_retrieval(
            query=query,
            citations=rag_result.citations,
            retrieval_context=rag_result.retrieval_context,
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
        )

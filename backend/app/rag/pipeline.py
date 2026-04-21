"""RAG 的可组合检索管道。

这一版把检索流程显式拆成 5 步：
- preprocess：输入规整
- build_query_bundle：query 改写 / 扩写
- retrieve_candidates：多 query 候选召回
- rerank_candidates：LLM / 规则重排
- format_context：证据压缩与结果组装

检索模式、Chat 图中的 RAG 节点、支持问题 Agent 都复用这一条链路。
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableLambda
from langsmith import traceable

from ..schemas import (
    Citation,
    RAGQueryBundle,
    RetrievalCandidateDebug,
    RetrievalDebugInfo,
    RetrievalProfile,
    RetrievalResult,
    RelatedDocumentLink,
    ScopeType,
)
from ..services.knowledge_store import KnowledgeStore
from ..services.llm_service import LLMService


class RAGPipeline:
    """面向学习项目的 scoped RAG 检索管道。"""

    def __init__(self, knowledge_store: KnowledgeStore, llm_service: LLMService) -> None:
        self.knowledge_store = knowledge_store
        self.llm_service = llm_service
        self.pipeline = (
            RunnableLambda(self._preprocess)
            | RunnableLambda(self._build_query_bundle)
            | RunnableLambda(self._retrieve_candidates)
            | RunnableLambda(self._rerank_candidates)
            | RunnableLambda(self._format_context)
        )

    def run(
        self,
        *,
        query: str,
        scope_type: ScopeType = "global",
        scope_id: str | None = None,
        model_config: Any,
        limit: int = 6,
        retrieval_profile: RetrievalProfile = "default",
        query_bundle_context: dict[str, Any] | None = None,
    ) -> RetrievalResult:
        return RetrievalResult.model_validate(
            self.pipeline.invoke(
                {
                    "query": query,
                    "scope_type": scope_type,
                    "scope_id": scope_id,
                    "limit": limit,
                    "model_config": model_config,
                    "retrieval_profile": retrieval_profile,
                    "query_bundle_context": query_bundle_context or {},
                }
            )
        )

    def _preprocess(self, payload: dict[str, Any]) -> dict[str, Any]:
        """输入规整，并保留 profile / context 供后续步骤使用。"""

        query = str(payload["query"]).strip()
        normalized_query = " ".join(
            query.replace("请根据文档", "").replace("请参考资料", "").replace("请帮我", "").split()
        ).strip() or query
        return {
            "query": query,
            "normalized_query": normalized_query,
            "scope_type": payload.get("scope_type", "global"),
            "scope_id": payload.get("scope_id"),
            "limit": int(payload.get("limit", 6)),
            "model_config": payload.get("model_config"),
            "retrieval_profile": payload.get("retrieval_profile", "default"),
            "query_bundle_context": payload.get("query_bundle_context", {}),
        }

    def _build_query_bundle(self, payload: dict[str, Any]) -> dict[str, Any]:
        """构造 query bundle。"""

        bundle = self.llm_service.build_rag_query_bundle(
            # 这里必须保留用户原始 query，
            # 让 bundle 里能同时看到 original / normalized / rewritten 三层表达。
            query=payload["query"],
            retrieval_profile=payload["retrieval_profile"],
            context=payload.get("query_bundle_context"),
            model_config=payload["model_config"],
        )
        return {
            **payload,
            "query_bundle": bundle.model_dump(mode="json"),
        }

    @traceable(name="rag_retrieve_candidates")
    def _retrieve_candidates(self, payload: dict[str, Any]) -> dict[str, Any]:
        """按 query bundle 做多路召回，并按 chunk 去重合并。"""

        bundle = RAGQueryBundle.model_validate(payload["query_bundle"])
        merged: dict[str, RetrievalCandidateDebug] = {}
        for variant in bundle.query_variants:
            candidates = self.knowledge_store.search_candidates(
                variant.query,
                limit=max(int(payload.get("limit", 6)) * 2, 12),
                scope_type=payload.get("scope_type", "global"),
                scope_id=payload.get("scope_id"),
                retrieval_profile=payload.get("retrieval_profile", "default"),
            )
            for candidate in candidates:
                existing = merged.get(candidate.chunk_id)
                if existing is None:
                    merged[candidate.chunk_id] = candidate.model_copy(
                        update={
                            "source_query": variant.query,
                            "query_label": variant.label,
                            "matched_query_labels": [variant.label],
                        }
                    )
                    continue

                merged[candidate.chunk_id] = existing.model_copy(
                    update={
                        "fused_score": existing.fused_score + candidate.fused_score + 0.04,
                        "lexical_score": max(existing.lexical_score, candidate.lexical_score),
                        "vector_score": max(existing.vector_score, candidate.vector_score),
                        "matched_query_labels": sorted(
                            {*(existing.matched_query_labels or []), variant.label}
                        ),
                    }
                )

        ranked = sorted(
            merged.values(),
            key=lambda item: (item.fused_score, len(item.matched_query_labels), item.lexical_score + item.vector_score),
            reverse=True,
        )
        top_candidates = ranked[:20]
        return {
            **payload,
            "query_bundle": bundle.model_dump(mode="json"),
            "candidates": [item.model_dump(mode="json") for item in top_candidates],
        }

    def _rerank_candidates(self, payload: dict[str, Any]) -> dict[str, Any]:
        """对候选做 rerank，并选出最终证据片段。"""

        bundle = RAGQueryBundle.model_validate(payload["query_bundle"])
        candidates = [RetrievalCandidateDebug.model_validate(item) for item in payload.get("candidates", [])]
        reranked = self.llm_service.rerank_retrieval_candidates(
            query=bundle.rewritten_query or bundle.normalized_query or bundle.original_query,
            candidates=candidates,
            retrieval_profile=payload.get("retrieval_profile", "default"),
            model_config=payload["model_config"],
        )

        threshold = 0.42 if payload.get("retrieval_profile", "default") == "support_issue" else 0.35
        selected = [
            item
            for item in reranked
            if item.useful_for_answer and item.relevance_score >= threshold
        ][: min(int(payload.get("limit", 6)), 6)]

        return {
            **payload,
            "query_bundle": bundle.model_dump(mode="json"),
            "rerank_preview": [item.model_dump(mode="json") for item in reranked[:12]],
            "selected_chunks": [item.model_dump(mode="json") for item in selected],
        }

    def _format_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        """把最终证据片段整理成 retrieval_context 和 debug 信息。"""

        bundle = RAGQueryBundle.model_validate(payload["query_bundle"])
        selected_chunks = [RetrievalCandidateDebug.model_validate(item) for item in payload.get("selected_chunks", [])]
        rerank_preview = [RetrievalCandidateDebug.model_validate(item) for item in payload.get("rerank_preview", [])]
        citations = [
            Citation(
                document_id=item.document_id,
                document_name=item.document_name,
                chunk_id=item.chunk_id,
                snippet=item.snippet,
                tree_id=item.tree_id,
                tree_path=item.tree_path,
                relative_path=item.relative_path,
                source_type=item.source_type,
                heading_path=item.heading_path,
                metadata=item.metadata,
            )
            for item in selected_chunks
        ]
        retrieval_context = "\n\n".join(
            "\n".join(
                [
                    f"[{index + 1}] {citation.document_name}",
                    f"标题路径：{citation.heading_path or citation.tree_path or '/'}",
                    f"文件路径：{citation.relative_path or citation.document_name}",
                    f"片段：{citation.snippet}",
                ]
            )
            for index, citation in enumerate(citations)
        )
        debug = RetrievalDebugInfo(
            retrieval_profile=payload.get("retrieval_profile", "default"),
            query_bundle=bundle,
            candidate_count=len(payload.get("candidates", [])),
            selected_count=len(selected_chunks),
            selected_chunks=selected_chunks,
            rerank_preview=rerank_preview,
        )
        return {
            "query": bundle.rewritten_query or bundle.normalized_query or bundle.original_query,
            "scope_type": payload.get("scope_type", "global"),
            "scope_id": payload.get("scope_id"),
            "citations": [citation.model_dump(mode="json") for citation in citations],
            "retrieval_context": retrieval_context,
            "summary": "",
            "debug": debug.model_dump(mode="json"),
        }

    def build_related_document_links(self, citations: list[Citation]) -> list[RelatedDocumentLink]:
        document_ids: list[str] = []
        seen_document_ids: set[str] = set()
        for citation in citations:
            if citation.document_id in seen_document_ids:
                continue
            seen_document_ids.add(citation.document_id)
            document_ids.append(citation.document_id)

        external_urls = self.knowledge_store.get_document_external_urls(document_ids)
        seen_urls: set[str] = set()
        related_links: list[RelatedDocumentLink] = []
        for citation in citations:
            external_url = external_urls.get(citation.document_id)
            if external_url is None or external_url in seen_urls:
                continue
            seen_urls.add(external_url)
            related_links.append(
                RelatedDocumentLink(
                    document_id=citation.document_id,
                    document_name=citation.document_name,
                    external_url=external_url,
                )
            )
        return related_links

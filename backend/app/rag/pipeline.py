"""RAG 的可组合检索管道。

这里继续保留 LCEL 的教学思路，把流程拆成 3 步：
- query 规整
- scope-aware 检索（当前底层是 Hybrid RAG）
- retrieval context 组装

检索模式、Chat 图中的 RAG 节点、配置型 Agent 都复用这一条链路。
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableLambda
from langsmith import traceable

from ..schemas import Citation, RelatedDocumentLink, RetrievalResult, ScopeType
from ..services.knowledge_store import KnowledgeStore


class RAGPipeline:
    """面向学习项目的 scoped RAG 检索管道。

    这里特意把 RAG 拆成 3 个 Runnable 节点，方便你对应 LCEL 思维：
    - `_rewrite_query`：输入规整
    - `_retrieve_documents`：真正检索
    - `_format_context`：把命中片段整理成可喂给 LLM 的上下文
    """

    def __init__(self, knowledge_store: KnowledgeStore) -> None:
        self.knowledge_store = knowledge_store
        self.pipeline = (
            RunnableLambda(self._rewrite_query)
            | RunnableLambda(self._retrieve_documents)
            | RunnableLambda(self._format_context)
        )

    def run(
        self,
        *,
        query: str,
        scope_type: ScopeType = "global",
        scope_id: str | None = None,
        limit: int = 6,
    ) -> RetrievalResult:
        return RetrievalResult.model_validate(
            self.pipeline.invoke(
                {
                    "query": query,
                    "scope_type": scope_type,
                    "scope_id": scope_id,
                    "limit": limit,
                }
            )
        )

    def _rewrite_query(self, payload: dict[str, Any]) -> dict[str, Any]:
        """轻量规整查询词，去掉演示型前缀语句。"""
        query = str(payload["query"]).strip()
        normalized_query = query.replace("请根据文档", "").replace("请参考资料", "").strip() or query
        return {
            "query": normalized_query,
            "scope_type": payload.get("scope_type", "global"),
            "scope_id": payload.get("scope_id"),
            "limit": int(payload.get("limit", 6)),
        }

    @traceable(name="rag_retrieve")
    def _retrieve_documents(self, payload: dict[str, Any]) -> dict[str, Any]:
        """真正执行 scoped 检索。

        注意这里的 retrieve 已经不是单一算法：
        `KnowledgeStore.search()` 会在 scope 范围内做 lexical + vector 双召回，
        再把结果融合成一组 citations。
        """
        citations = self.knowledge_store.search(
            payload["query"],
            limit=int(payload.get("limit", 6)),
            scope_type=payload.get("scope_type", "global"),
            scope_id=payload.get("scope_id"),
        )
        return {
            "query": payload["query"],
            "scope_type": payload.get("scope_type", "global"),
            "scope_id": payload.get("scope_id"),
            "citations": [citation.model_dump() for citation in citations],
        }

    def _format_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        """把 citation 列表整理成 retrieval_context 字符串。"""
        citations = [Citation.model_validate(item) for item in payload.get("citations", [])]
        retrieval_context = "\n\n".join(
            f"[{index + 1}] {citation.document_name} ({citation.tree_path or '/'})\n{citation.snippet}"
            for index, citation in enumerate(citations)
        )
        return {
            "query": payload["query"],
            "scope_type": payload.get("scope_type", "global"),
            "scope_id": payload.get("scope_id"),
            "citations": [citation.model_dump() for citation in citations],
            "retrieval_context": retrieval_context,
            "summary": "",
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

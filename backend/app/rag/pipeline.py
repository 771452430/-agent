"""RAG 的 LCEL 管道。

这里刻意不用一大坨函数直接调用，而是拆成多个 RunnableLambda 节点，
帮助学习 LangChain 的“可组合链”思维：
query 预处理 -> 检索 -> 上下文整理
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableLambda
from langsmith import traceable

from ..schemas import Citation, KnowledgeSearchResponse
from ..services.knowledge_store import KnowledgeStore


class RAGPipeline:
    """面向学习的检索增强管道。"""

    def __init__(self, knowledge_store: KnowledgeStore) -> None:
        self.knowledge_store = knowledge_store
        self.pipeline = (
            RunnableLambda(self._rewrite_query)
            | RunnableLambda(self._retrieve_documents)
            | RunnableLambda(self._format_context)
        )

    def run(self, query: str, limit: int = 4) -> KnowledgeSearchResponse:
        return KnowledgeSearchResponse.model_validate(self.pipeline.invoke({"query": query, "limit": limit}))

    def _rewrite_query(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload["query"]).strip()
        return {"query": query.replace("请根据文档", "").strip() or query, "limit": payload.get("limit", 4)}

    @traceable(name="rag_retrieve")
    def _retrieve_documents(self, payload: dict[str, Any]) -> dict[str, Any]:
        citations = self.knowledge_store.search(payload["query"], limit=int(payload.get("limit", 4)))
        return {"query": payload["query"], "citations": [citation.model_dump() for citation in citations]}

    def _format_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        citations = [Citation.model_validate(item) for item in payload.get("citations", [])]
        retrieval_context = "\n\n".join(
            f"[{index + 1}] {citation.document_name}: {citation.snippet}"
            for index, citation in enumerate(citations)
        )
        return {
            "query": payload["query"],
            "citations": [citation.model_dump() for citation in citations],
            "retrieval_context": retrieval_context,
        }

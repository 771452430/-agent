"""RAG 提准改造的专项回归测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.rag.pipeline import RAGPipeline
from app.schemas import ModelConfig, RAGQueryBundle, RAGQueryVariant, RetrievalCandidateDebug
from app.services.knowledge_store import KnowledgeStore
from app.services.llm_service import LLMService
from app.services.provider_store import ProviderStore


class _StubKnowledgeStore:
    """用最小假实现验证多 query 合并和 debug 输出。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def search_candidates(
        self,
        query: str,
        *,
        limit: int = 6,
        scope_type: str = "global",
        scope_id: str | None = None,
        retrieval_profile: str = "default",
    ) -> list[RetrievalCandidateDebug]:
        self.calls.append((query, retrieval_profile))
        base = [
            RetrievalCandidateDebug(
                chunk_id="chunk-1",
                document_id="doc-1",
                document_name="已审核案例",
                snippet="工作台登录失败，错误码 E401，建议先检查租户权限。",
                heading_path="工作台 / 登录",
                metadata={"source": "approved_case"},
                fused_score=0.35,
            ),
            RetrievalCandidateDebug(
                chunk_id="chunk-2",
                document_id="doc-2",
                document_name="普通 FAQ",
                snippet="通用登录失败排查步骤。",
                heading_path="FAQ / 登录",
                metadata={"source": "document"},
                fused_score=0.22,
            ),
        ]
        if "错误码" in query:
            return base[:1]
        return base

    def get_document_external_urls(self, document_ids: list[str]) -> dict[str, str]:
        return {document_id: f"https://doc.example.com/{document_id}" for document_id in document_ids}


class _StubLLMService:
    """把 query rewrite 和 rerank 固定下来，避免测试依赖真实模型。"""

    def build_rag_query_bundle(
        self,
        *,
        query: str,
        retrieval_profile: str,
        context: dict[str, object] | None,
        model_config: ModelConfig,
    ) -> RAGQueryBundle:
        del model_config
        module_value = str((context or {}).get("module_value") or "").strip()
        keyword_query = f"{module_value} 错误码 E401".strip()
        query_variants = [RAGQueryVariant(label="original", query=query, source="original")]
        if keyword_query:
            query_variants.append(RAGQueryVariant(label="keyword_1", query=keyword_query, source="keyword"))
        return RAGQueryBundle(
            original_query=query,
            normalized_query=query,
            rewritten_query=query,
            keyword_queries=[keyword_query] if keyword_query else [],
            sub_queries=[],
            must_terms=["E401"],
            filters={"module": module_value} if retrieval_profile == "support_issue" and module_value else {},
            query_variants=query_variants,
        )

    def rerank_retrieval_candidates(
        self,
        *,
        query: str,
        candidates: list[RetrievalCandidateDebug],
        retrieval_profile: str,
        model_config: ModelConfig,
    ) -> list[RetrievalCandidateDebug]:
        del query, retrieval_profile, model_config
        reranked: list[RetrievalCandidateDebug] = []
        for index, candidate in enumerate(candidates):
            reranked.append(
                candidate.model_copy(
                    update={
                        "relevance_score": 0.91 if index == 0 else 0.12,
                        "useful_for_answer": index == 0,
                        "reason": "标题路径和错误码均命中。" if index == 0 else "相关性不足。",
                    }
                )
            )
        return reranked


class RAGPipelineUpgradeTests(unittest.TestCase):
    """覆盖 query bundle、结构化 chunk、profile boost 和 debug 输出。"""

    def _learning_model(self) -> ModelConfig:
        return ModelConfig(mode="learning", provider="mock", model="learning-mode")

    def test_learning_mode_query_bundle_preserves_original_query(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            provider_store = ProviderStore(Path(temp_dir) / "providers.sqlite")
            llm_service = LLMService(provider_store=provider_store)
            original_query = "请帮我看下 工作台 登录失败 E401"
            bundle = llm_service.build_rag_query_bundle(
                query=original_query,
                retrieval_profile="support_issue",
                context={
                    "question": "工作台 登录失败 E401",
                    "module_value": "工作台",
                    "category": "登录",
                },
                model_config=self._learning_model(),
            )

        self.assertEqual(bundle.original_query, original_query)
        self.assertTrue(any(item.label == "original" for item in bundle.query_variants))
        self.assertEqual(bundle.filters.get("module"), "工作台")
        self.assertTrue(any("E401" in item for item in bundle.must_terms))
        self.assertTrue(any("工作台" in item.query for item in bundle.query_variants))

    def test_structured_chunking_keeps_heading_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = KnowledgeStore(
                Path(temp_dir) / "knowledge.sqlite",
                Path(temp_dir) / "chroma",
            )
            chunks = store._split_text(
                "# 登录问题\n用户打开工作台后提示登录失败。\n\n- 第一步：检查租户权限\n- 第二步：清理浏览器缓存\n\n## 错误码\nE401 表示当前账号未分配访问权限。\n",
                chunk_size=80,
                overlap=10,
            )

        self.assertTrue(any(item.heading_path == "登录问题" and "登录失败" in item.content for item in chunks))
        self.assertTrue(any(item.heading_path == "登录问题" and "第一步" in item.content for item in chunks))
        self.assertTrue(any(item.heading_path == "登录问题 / 错误码" and "E401" in item.content for item in chunks))

    def test_support_issue_profile_boosts_approved_case(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = KnowledgeStore(
                Path(temp_dir) / "knowledge.sqlite",
                Path(temp_dir) / "chroma",
            )
            # 这里显式关闭向量召回，让测试只关注 lexical + profile boost 的排序差异。
            store.vector_store = None

            approved_doc = store.ingest_document(
                "approved-case.md",
                "工作台 登录失败 E401 处理步骤".encode("utf-8"),
                relative_path="支持案例库/工作台/approved-case.md",
                metadata={"source": "approved_case", "question_category": "登录"},
            )
            general_doc = store.ingest_document(
                "general-guide.md",
                "工作台 登录失败 E401 处理步骤".encode("utf-8"),
                relative_path="知识文档/工作台/general-guide.md",
                metadata={"source": "document"},
            )

            default_candidates = store.search_candidates(
                "工作台 登录失败 E401",
                limit=2,
                retrieval_profile="default",
            )
            support_candidates = store.search_candidates(
                "工作台 登录失败 E401",
                limit=2,
                retrieval_profile="support_issue",
            )

        self.assertEqual(default_candidates[0].document_id, general_doc.id)
        self.assertEqual(support_candidates[0].document_id, approved_doc.id)
        self.assertEqual(support_candidates[0].metadata.get("source"), "approved_case")

    def test_pipeline_returns_debug_payload_and_selected_chunks(self) -> None:
        knowledge_store = _StubKnowledgeStore()
        pipeline = RAGPipeline(knowledge_store, _StubLLMService())

        result = pipeline.run(
            query="工作台登录失败",
            scope_type="global",
            scope_id=None,
            model_config=self._learning_model(),
            retrieval_profile="support_issue",
            query_bundle_context={"module_value": "工作台"},
        )

        self.assertIsNotNone(result.debug)
        assert result.debug is not None
        self.assertEqual(result.debug.retrieval_profile, "support_issue")
        self.assertEqual(result.debug.candidate_count, 2)
        self.assertEqual(result.debug.selected_count, 1)
        self.assertEqual(len(result.citations), 1)
        self.assertEqual(result.citations[0].document_id, "doc-1")
        self.assertEqual(result.citations[0].heading_path, "工作台 / 登录")
        self.assertEqual(knowledge_store.calls[0][1], "support_issue")
        self.assertTrue(any(item.query == "工作台 错误码 E401" for item in result.debug.query_bundle.query_variants))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

"""RAG embedding 设置的回归测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.schemas import ProviderModel, UpdateRAGEmbeddingSettingsRequest
from app.services.embedding_service import EmbeddingService
from app.services.provider_store import ProviderStore
from app.services.rag_embedding_settings_service import RAGEmbeddingSettingsService
from app.services.rag_embedding_settings_store import RAGEmbeddingSettingsStore
from app.settings import AppSettings


class RAGEmbeddingSettingsTests(unittest.TestCase):
    """覆盖环境变量优先级和数据库设置回退。"""

    def _build_provider_store(self, temp_dir: str) -> ProviderStore:
        provider_store = ProviderStore(Path(temp_dir) / "providers.sqlite")
        provider_store.update_provider(
            "custom_openai",
            enabled=True,
            api_base_url="https://embedding.example.com/v1",
            api_key="test-key",
            models=[ProviderModel(id="text-embedding-3-small", label="text-embedding-3-small", source="manual")],
        )
        return provider_store

    def test_database_embedding_settings_are_used_when_env_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            provider_store = self._build_provider_store(temp_dir)
            rag_store = RAGEmbeddingSettingsStore(Path(temp_dir) / "rag.sqlite")
            rag_service = RAGEmbeddingSettingsService(rag_store, AppSettings())
            rag_service.update_rag_embedding_settings(
                UpdateRAGEmbeddingSettingsRequest(
                    provider_id="custom_openai",
                    model="text-embedding-3-small",
                    timeout_seconds=18,
                )
            )

            service = EmbeddingService(
                provider_store=provider_store,
                settings=AppSettings(),
                rag_embedding_settings_service=rag_service,
            )
            selection = service.describe_runtime_selection()

        self.assertEqual(selection.source, "database")
        self.assertEqual(selection.provider_id, "custom_openai")
        self.assertEqual(selection.model_name, "text-embedding-3-small")
        self.assertEqual(selection.timeout_seconds, 18)
        self.assertEqual(selection.preferred_backend, "openai_compatible:text-embedding-3-small")

    def test_environment_settings_override_database_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            provider_store = self._build_provider_store(temp_dir)
            rag_store = RAGEmbeddingSettingsStore(Path(temp_dir) / "rag.sqlite")
            rag_service = RAGEmbeddingSettingsService(rag_store, AppSettings())
            rag_service.update_rag_embedding_settings(
                UpdateRAGEmbeddingSettingsRequest(
                    provider_id="custom_openai",
                    model="text-embedding-3-small",
                    timeout_seconds=18,
                )
            )

            service = EmbeddingService(
                provider_store=provider_store,
                settings=AppSettings(
                    rag_embedding_provider="custom_openai",
                    rag_embedding_model="text-embedding-3-large",
                    rag_embedding_timeout_seconds=35,
                ),
                rag_embedding_settings_service=rag_service,
            )
            selection = service.describe_runtime_selection()

        self.assertEqual(selection.source, "environment")
        self.assertEqual(selection.model_name, "text-embedding-3-large")
        self.assertEqual(selection.timeout_seconds, 35)
        self.assertEqual(selection.preferred_backend, "openai_compatible:text-embedding-3-large")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

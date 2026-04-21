"""RAG embedding 设置服务。"""

from __future__ import annotations

from datetime import datetime, timezone

from ..schemas import (
    RAGEmbeddingRuntimeSettings,
    UpdateRAGEmbeddingSettingsRequest,
)
from ..settings import AppSettings
from .rag_embedding_settings_store import RAGEmbeddingSettingsStore


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RAGEmbeddingSettingsService:
    """统一处理 RAG embedding 配置读取与保存。"""

    def __init__(self, rag_embedding_store: RAGEmbeddingSettingsStore, app_settings: AppSettings) -> None:
        self.rag_embedding_store = rag_embedding_store
        self.app_settings = app_settings

    def get_runtime_settings(self) -> RAGEmbeddingRuntimeSettings:
        stored = self.rag_embedding_store.get_runtime_settings()
        if stored is not None:
            return stored
        return self.rag_embedding_store.blank_runtime(timeout_seconds=self.app_settings.rag_embedding_timeout_seconds)

    def update_rag_embedding_settings(self, request_data: UpdateRAGEmbeddingSettingsRequest) -> RAGEmbeddingRuntimeSettings:
        current = self.get_runtime_settings()
        provider_id = current.provider_id
        model = current.model
        timeout_seconds = current.timeout_seconds

        if request_data.provider_id is not None:
            provider_id = request_data.provider_id.strip() or None
        if request_data.model is not None:
            model = request_data.model.strip() or None
        if request_data.timeout_seconds is not None:
            timeout_seconds = max(5, int(request_data.timeout_seconds))

        # provider/model 任一为空时，表示显式回退到本地 hashing。
        if not provider_id or not model:
            provider_id = None
            model = None

        return self.rag_embedding_store.save_runtime_settings(
            RAGEmbeddingRuntimeSettings(
                provider_id=provider_id,
                model=model,
                timeout_seconds=max(5, int(timeout_seconds)),
                created_at=current.created_at,
                updated_at=_utc_now(),
            )
        )

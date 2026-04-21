"""检索向量化服务。

这一层同时支持两种 embedding backend：
- 默认本地 `HashingVectorizer`，保证离线学习模式稳定可跑；
- 可选复用全局 provider 配置，走真实 embedding 接口提高语义召回质量。

配置来源分两层，环境变量优先：
- `RAG_EMBEDDING_PROVIDER`
- `RAG_EMBEDDING_MODEL`
- `RAG_EMBEDDING_TIMEOUT_SECONDS`

如果环境变量没配，则回退到 SQLite 里的全局 RAG embedding 设置。
当真实 embedding 配置缺失或调用失败时，会自动回退到 hashing backend。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib import error as urlerror
from urllib import request

from sklearn.feature_extraction.text import HashingVectorizer

from ..settings import AppSettings
from .provider_store import ProviderStore

if False:  # pragma: no cover
    from .rag_embedding_settings_service import RAGEmbeddingSettingsService


@dataclass(frozen=True)
class EmbeddingRuntimeSelection:
    """描述当前 embedding 运行时会优先采用哪套配置。"""

    source: str
    provider_id: str
    model_name: str
    timeout_seconds: int
    preferred_backend: str


class EmbeddingService:
    """把文本转换为固定维度向量。"""

    def __init__(
        self,
        *,
        provider_store: ProviderStore | None = None,
        settings: AppSettings | None = None,
        rag_embedding_settings_service: RAGEmbeddingSettingsService | None = None,
        n_features: int = 768,
    ) -> None:
        self.provider_store = provider_store
        self.settings = settings
        self.rag_embedding_settings_service = rag_embedding_settings_service
        self.n_features = n_features
        self._hashing_model_name = f"hashing-char-ngram-{n_features}-v1"
        self.vectorizer = HashingVectorizer(
            n_features=n_features,
            alternate_sign=False,
            norm="l2",
            lowercase=True,
            analyzer="char",
            ngram_range=(2, 4),
        )
        self.model_name = self.describe_runtime_selection().preferred_backend

    def _normalize_texts(self, texts: Sequence[str]) -> list[str]:
        return [str(text or "").strip() for text in texts if str(text or "").strip() != ""]

    @property
    def hashing_model_name(self) -> str:
        return self._hashing_model_name

    def _resolve_requested_runtime(self) -> tuple[str, str, int, str] | None:
        """解析“用户希望用哪套 embedding 配置”。

        返回值依次为：
        - provider_id
        - model_name
        - timeout_seconds
        - source（environment / database）
        """

        if self.settings is not None:
            env_provider_id = (self.settings.rag_embedding_provider or "").strip()
            env_model_name = (self.settings.rag_embedding_model or "").strip()
            if env_provider_id != "" and env_model_name != "":
                return (
                    env_provider_id,
                    env_model_name,
                    max(5, int(self.settings.rag_embedding_timeout_seconds)),
                    "environment",
                )

        if self.rag_embedding_settings_service is not None:
            runtime = self.rag_embedding_settings_service.get_runtime_settings()
            provider_id = (runtime.provider_id or "").strip()
            model_name = (runtime.model or "").strip()
            if provider_id != "" and model_name != "":
                return (
                    provider_id,
                    model_name,
                    max(5, int(runtime.timeout_seconds)),
                    "database",
                )
        return None

    def _resolve_embedding_runtime(self) -> tuple[str, str, str, int] | None:
        """返回真实 embedding 运行时配置。

        返回值依次为：
        - protocol
        - api_base_url
        - api_key
        - timeout_seconds
        """

        if self.provider_store is None:
            return None

        requested = self._resolve_requested_runtime()
        if requested is None:
            return None
        provider_id, model_name, timeout_seconds, _source = requested

        provider = self.provider_store.get_runtime_provider(provider_id)
        if provider is None or not provider.enabled:
            return None
        if provider.api_base_url.strip() == "":
            return None
        if provider.protocol not in {"openai_compatible", "ollama_native"}:
            return None
        # OpenAI-compatible provider 需要 API Key，
        # 但本地 Ollama 往往没有鉴权，这里不能一刀切拦掉。
        if provider.protocol == "openai_compatible" and not provider.api_key:
            return None

        return (
            provider.protocol,
            provider.api_base_url.strip(),
            model_name,
            timeout_seconds,
        )

    def describe_runtime_selection(self) -> EmbeddingRuntimeSelection:
        """返回当前 embedding 配置的优先来源与期望 backend。"""

        requested = self._resolve_requested_runtime()
        if requested is None:
            return EmbeddingRuntimeSelection(
                source="fallback",
                provider_id="",
                model_name="",
                timeout_seconds=max(5, int(self.settings.rag_embedding_timeout_seconds)) if self.settings else 20,
                preferred_backend=self._hashing_model_name,
            )

        provider_id, model_name, timeout_seconds, source = requested
        runtime = self._resolve_embedding_runtime()
        if runtime is None:
            return EmbeddingRuntimeSelection(
                source=source,
                provider_id=provider_id,
                model_name=model_name,
                timeout_seconds=timeout_seconds,
                preferred_backend=self._hashing_model_name,
            )
        protocol, _api_base_url, model_name, _timeout = runtime
        return EmbeddingRuntimeSelection(
            source=source,
            provider_id=provider_id,
            model_name=model_name,
            timeout_seconds=timeout_seconds,
            preferred_backend=f"{protocol}:{model_name}",
        )

    def _hashing_embeddings(self, texts: Sequence[str]) -> list[list[float]]:
        normalized = self._normalize_texts(texts)
        if not normalized:
            return []
        matrix = self.vectorizer.transform(normalized)
        return matrix.toarray().astype("float32").tolist()

    def _post_json(self, *, url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(url, data=body, method="POST")
        for key, value in headers.items():
            req.add_header(key, value)
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _candidate_openai_embedding_urls(self, api_base_url: str) -> list[str]:
        """生成 OpenAI-compatible embedding 候选地址。

        部分第三方兼容网关把 chat 放在：
        - `/chat/completions`
        - `/v1/chat/completions`

        embedding 往往也有同样的差异，因此这里和 LLMService 保持一致，
        依次尝试：
        - `{base}/embeddings`
        - `{base}/v1/embeddings`
        """

        base = api_base_url.rstrip("/")
        candidates = [base + "/embeddings"]
        if not base.endswith("/v1"):
            candidates.append(base + "/v1/embeddings")
        return list(dict.fromkeys(candidates))

    def _openai_embeddings(
        self,
        *,
        api_base_url: str,
        api_key: str,
        model_name: str,
        texts: list[str],
        timeout: int,
    ) -> list[list[float]]:
        payload = {"model": model_name, "input": texts}
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        last_error: Exception | None = None
        data: dict[str, Any] | None = None
        for url in self._candidate_openai_embedding_urls(api_base_url):
            try:
                data = self._post_json(
                    url=url,
                    payload=payload,
                    headers=headers,
                    timeout=timeout,
                )
                last_error = None
                break
            except (urlerror.HTTPError, urlerror.URLError, TimeoutError, ValueError) as exc:
                last_error = exc
                continue
        if data is None:
            if last_error is not None:
                raise last_error
            raise RuntimeError("embedding 请求未返回有效响应")
        raw_items = data.get("data")
        if not isinstance(raw_items, list):
            raise RuntimeError("embedding 响应缺少 data 列表")

        vectors: list[list[float]] = []
        for item in raw_items:
            if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
                raise RuntimeError("embedding 响应项缺少 embedding")
            vectors.append([float(value) for value in item["embedding"]])
        return vectors

    def _ollama_embeddings(
        self,
        *,
        api_base_url: str,
        model_name: str,
        texts: list[str],
        timeout: int,
    ) -> list[list[float]]:
        url = api_base_url.rstrip("/") + "/api/embed"
        data = self._post_json(
            url=url,
            payload={"model": model_name, "input": texts},
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        raw_vectors = data.get("embeddings")
        if not isinstance(raw_vectors, list):
            raise RuntimeError("ollama embedding 响应缺少 embeddings")
        vectors: list[list[float]] = []
        for item in raw_vectors:
            if not isinstance(item, list):
                raise RuntimeError("ollama embedding 响应项格式不正确")
            vectors.append([float(value) for value in item])
        return vectors

    def _provider_embeddings(self, texts: list[str]) -> list[list[float]] | None:
        requested = self._resolve_requested_runtime()
        runtime = self._resolve_embedding_runtime()
        if runtime is None or requested is None or self.provider_store is None:
            return None

        provider_id, _requested_model_name, _requested_timeout, _source = requested
        protocol, api_base_url, model_name, timeout = runtime
        provider = self.provider_store.get_runtime_provider(provider_id)
        api_key = provider.api_key if provider is not None and provider.api_key else ""

        try:
            if protocol == "openai_compatible":
                if api_key == "":
                    return None
                return self._openai_embeddings(
                    api_base_url=api_base_url,
                    api_key=api_key,
                    model_name=model_name,
                    texts=texts,
                    timeout=timeout,
                )
            if protocol == "ollama_native":
                return self._ollama_embeddings(
                    api_base_url=api_base_url,
                    model_name=model_name,
                    texts=texts,
                    timeout=timeout,
                )
        except (RuntimeError, urlerror.URLError, TimeoutError, ValueError):
            return None
        return None

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """批量向量化文档或 chunk。"""

        normalized = self._normalize_texts(texts)
        if not normalized:
            return []

        provider_vectors = self._provider_embeddings(normalized)
        if provider_vectors is not None and len(provider_vectors) == len(normalized):
            self.model_name = self.describe_runtime_selection().preferred_backend
            return provider_vectors

        self.model_name = self._hashing_model_name
        return self._hashing_embeddings(normalized)

    def embed_query(self, text: str) -> list[float]:
        """向量化单条查询。"""

        vectors = self.embed_documents([text])
        if vectors:
            return vectors[0]
        return [0.0] * self.n_features

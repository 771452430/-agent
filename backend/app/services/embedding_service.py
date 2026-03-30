"""本地 embedding 服务。

这层故意单独拆出来，是为了把“文本转向量”从知识库管理逻辑里分离出来：
- `KnowledgeStore` 负责文档、chunk、知识树和检索范围；
- `EmbeddingService` 只负责把文本映射到统一向量空间；
- `VectorStore` 再负责向量持久化和近邻查询。

当前默认使用本地 `HashingVectorizer`：
- 不依赖联网下载模型，离线环境也能直接运行；
- 文档和查询天然使用同一套向量化规则；
- 很适合作为学习版 Hybrid RAG 的稳定起点。

注意：
- 这不是“大模型语义 embedding”，更像是一个本地可复现的轻量 embedding backend；
- 后续你完全可以把这里替换成 OpenAI / Ollama / sentence-transformers。
"""

from __future__ import annotations

from collections.abc import Sequence

from sklearn.feature_extraction.text import HashingVectorizer


class EmbeddingService:
    """把文本转换为固定维度向量。"""

    def __init__(self, n_features: int = 768) -> None:
        self.n_features = n_features
        self.model_name = f"hashing-char-ngram-{n_features}-v1"
        self.vectorizer = HashingVectorizer(
            n_features=n_features,
            alternate_sign=False,
            norm="l2",
            lowercase=True,
            analyzer="char",
            ngram_range=(2, 4),
        )

    def _normalize_texts(self, texts: Sequence[str]) -> list[str]:
        return [str(text or "").strip() for text in texts]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """批量向量化文档或 chunk。"""

        normalized = self._normalize_texts(texts)
        if not normalized:
            return []
        matrix = self.vectorizer.transform(normalized)
        return matrix.toarray().astype("float32").tolist()

    def embed_query(self, text: str) -> list[float]:
        """向量化单条查询。"""

        vectors = self.embed_documents([text])
        if vectors:
            return vectors[0]
        return [0.0] * self.n_features

"""Chroma 向量存储适配层。

这一层只做两件事：
1. 用稳定的 chunk id 把向量和 metadata 存进 Chroma；
2. 按 scope 过滤条件执行向量召回。

把这部分单独抽出来后，你后面如果想把 Chroma 换成 pgvector / Milvus，
主要改这个文件即可，不必把 `KnowledgeStore` 和 `RAGPipeline` 一起推倒重来。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings


@dataclass(frozen=True)
class ChunkVectorRecord:
    """一个可写入向量库的 chunk。"""

    chunk_id: str
    document_id: str
    node_id: str
    document_name: str
    content: str
    tree_path: str
    relative_path: str
    source_type: str


@dataclass(frozen=True)
class VectorSearchHit:
    """向量检索命中的结果。"""

    chunk_id: str
    document_id: str
    node_id: str
    document_name: str
    content: str
    tree_path: str
    relative_path: str
    source_type: str
    distance: float


class KnowledgeVectorStore:
    """基于 Chroma 的知识库向量索引。"""

    def __init__(self, chroma_dir: Path, collection_name: str = "knowledge_chunks") -> None:
        self.backend_name = "chroma-cosine"
        self.collection_name = collection_name
        self.client = chromadb.PersistentClient(
            path=str(chroma_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        self.collection = self._get_or_create_collection()

    def _get_or_create_collection(self):
        return self.client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def reset(self) -> None:
        """重置整张向量表。

        这个 demo 版实现优先考虑“容易理解”：
        当我们检测到 SQLite chunk 数和 Chroma 不一致时，直接全量重建最直观。
        """

        try:
            self.client.delete_collection(self.collection_name)
        except Exception:
            pass
        self.collection = self._get_or_create_collection()

    def count(self) -> int:
        return int(self.collection.count())

    def upsert_chunks(self, records: list[ChunkVectorRecord], embeddings: list[list[float]]) -> None:
        if not records:
            return
        self.collection.upsert(
            ids=[record.chunk_id for record in records],
            documents=[record.content for record in records],
            metadatas=[
                {
                    "document_id": record.document_id,
                    "node_id": record.node_id,
                    "document_name": record.document_name,
                    "tree_path": record.tree_path,
                    "relative_path": record.relative_path,
                    "source_type": record.source_type,
                }
                for record in records
            ],
            embeddings=embeddings,
        )

    def delete_chunks(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        self.collection.delete(ids=chunk_ids)

    def query(self, query_embedding: list[float], limit: int, node_ids: list[str] | None = None) -> list[VectorSearchHit]:
        result_limit = min(limit, self.count())
        if result_limit <= 0:
            return []
        where: dict[str, Any] | None = None
        if node_ids:
            where = {"node_id": node_ids[0]} if len(node_ids) == 1 else {"node_id": {"$in": node_ids}}

        result = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=result_limit,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        ids = result.get("ids", [[]])[0]
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]

        hits: list[VectorSearchHit] = []
        for chunk_id, document, metadata, distance in zip(ids, documents, metadatas, distances):
            if not isinstance(metadata, dict):
                metadata = {}
            hits.append(
                VectorSearchHit(
                    chunk_id=str(chunk_id),
                    document_id=str(metadata.get("document_id") or ""),
                    node_id=str(metadata.get("node_id") or ""),
                    document_name=str(metadata.get("document_name") or ""),
                    content=str(document or ""),
                    tree_path=str(metadata.get("tree_path") or "/"),
                    relative_path=str(metadata.get("relative_path") or ""),
                    source_type=str(metadata.get("source_type") or "txt"),
                    distance=float(distance if distance is not None else 1.0),
                )
            )
        return hits

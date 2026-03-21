"""知识库与文档索引服务。

RAG 学习的关键不只是“调一个 retriever”，而是要理解整条链路：
loader -> splitter -> index -> retrieve -> citation。
因此这里把文档解析、切分、索引、检索集中封装，便于对照 docs/rag.md 学习。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterable
from uuid import uuid4

from langchain_core.documents import Document

try:
    from langchain_core.embeddings import FakeEmbeddings
except ImportError:  # pragma: no cover
    FakeEmbeddings = None

try:
    from langchain_chroma import Chroma
except Exception:  # pragma: no cover
    Chroma = None

from ..schemas import Citation, KnowledgeDocument


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class KnowledgeStore:
    """管理知识库文档、chunk 与检索。"""

    def __init__(self, sqlite_path: Path, chroma_dir: Path) -> None:
        self.sqlite_path = sqlite_path
        self.chroma_dir = chroma_dir
        self._init_db()
        self._embedding_backend = "lexical"
        self._vector_store = self._create_vector_store()
        self._rebuild_index()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_documents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    error_message TEXT
                );

                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    document_name TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def _create_vector_store(self):
        if Chroma and FakeEmbeddings:
            self._embedding_backend = "chroma"
            return Chroma(
                collection_name="learning_demo_knowledge",
                embedding_function=FakeEmbeddings(size=64),
                persist_directory=str(self.chroma_dir),
            )
        self._embedding_backend = "lexical"
        return None

    def _rebuild_index(self) -> None:
        if self._vector_store is None:
            return
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, document_id, document_name, content FROM knowledge_chunks ORDER BY created_at ASC"
            ).fetchall()
        if not rows:
            return
        docs = [
            Document(
                page_content=row["content"],
                metadata={
                    "chunk_id": row["id"],
                    "document_id": row["document_id"],
                    "document_name": row["document_name"],
                },
            )
            for row in rows
        ]
        self._vector_store.add_documents(docs)

    def list_documents(self) -> list[KnowledgeDocument]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_documents ORDER BY created_at DESC"
            ).fetchall()
        return [
            KnowledgeDocument(
                id=row["id"],
                name=row["name"],
                type=row["type"],
                status=row["status"],
                chunk_count=row["chunk_count"],
                created_at=datetime.fromisoformat(row["created_at"]),
                error_message=row["error_message"],
            )
            for row in rows
        ]

    def has_documents(self) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM knowledge_documents WHERE status = 'ready'"
            ).fetchone()
        return bool(row and row["count"] > 0)

    def ingest_document(self, filename: str, file_bytes: bytes) -> KnowledgeDocument:
        document_id = str(uuid4())
        file_type = Path(filename).suffix.lower().lstrip(".") or "txt"
        created_at = _utc_now()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_documents (id, name, type, status, chunk_count, created_at, error_message)
                VALUES (?, ?, ?, 'processing', 0, ?, NULL)
                """,
                (document_id, filename, file_type, created_at.isoformat()),
            )

        try:
            raw_text = self._extract_text(file_type=file_type, file_bytes=file_bytes)
            chunks = self._split_text(raw_text)
            self._store_chunks(document_id=document_id, document_name=filename, chunks=chunks)
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE knowledge_documents
                    SET status = 'ready', chunk_count = ?, error_message = NULL
                    WHERE id = ?
                    """,
                    (len(chunks), document_id),
                )
        except Exception as exc:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE knowledge_documents
                    SET status = 'error', error_message = ?
                    WHERE id = ?
                    """,
                    (str(exc), document_id),
                )

        result = next(item for item in self.list_documents() if item.id == document_id)
        return result

    def _extract_text(self, file_type: str, file_bytes: bytes) -> str:
        if file_type in {"txt", "md"}:
            return file_bytes.decode("utf-8", errors="ignore")
        if file_type == "pdf":
            try:
                from pypdf import PdfReader
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("需要安装 pypdf 才能解析 PDF") from exc
            with NamedTemporaryFile(suffix=".pdf") as temp_file:
                temp_file.write(file_bytes)
                temp_file.flush()
                reader = PdfReader(temp_file.name)
                return "\n".join(page.extract_text() or "" for page in reader.pages)
        if file_type == "docx":
            try:
                from docx import Document as DocxDocument
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("需要安装 python-docx 才能解析 DOCX") from exc
            with NamedTemporaryFile(suffix=".docx") as temp_file:
                temp_file.write(file_bytes)
                temp_file.flush()
                document = DocxDocument(temp_file.name)
                return "\n".join(paragraph.text for paragraph in document.paragraphs)
        raise RuntimeError(f"暂不支持的文档类型：{file_type}")

    def _split_text(self, text: str, chunk_size: int = 700, overlap: int = 100) -> list[str]:
        cleaned = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        if not cleaned:
            raise RuntimeError("文档解析后没有可索引内容")
        chunks: list[str] = []
        start = 0
        while start < len(cleaned):
            end = min(len(cleaned), start + chunk_size)
            chunks.append(cleaned[start:end])
            if end == len(cleaned):
                break
            start = max(0, end - overlap)
        return chunks

    def _store_chunks(self, document_id: str, document_name: str, chunks: Iterable[str]) -> None:
        chunk_rows = []
        docs = []
        now = _utc_now().isoformat()
        for index, content in enumerate(chunks):
            chunk_id = str(uuid4())
            chunk_rows.append((chunk_id, document_id, document_name, index, content, now))
            docs.append(
                Document(
                    page_content=content,
                    metadata={"chunk_id": chunk_id, "document_id": document_id, "document_name": document_name},
                )
            )
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO knowledge_chunks (id, document_id, document_name, chunk_index, content, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                chunk_rows,
            )
        if self._vector_store is not None and docs:
            self._vector_store.add_documents(docs)

    def search(self, query: str, limit: int = 4) -> list[Citation]:
        tokens = {token for token in query.lower().replace("，", " ").replace("。", " ").split() if token}
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT kc.id, kc.document_id, kc.document_name, kc.content
                FROM knowledge_chunks kc
                JOIN knowledge_documents kd ON kd.id = kc.document_id
                WHERE kd.status = 'ready'
                ORDER BY kc.created_at DESC
                """
            ).fetchall()

        scored = []
        for row in rows:
            content = row["content"]
            lowered = content.lower()
            score = sum(1 for token in tokens if token in lowered)
            if any(char in content for char in query[:20]):
                score += 1
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)

        return [
            Citation(
                document_id=row["document_id"],
                document_name=row["document_name"],
                chunk_id=row["id"],
                snippet=row["content"][:220],
            )
            for _, row in scored[:limit]
        ]

    @property
    def backend_name(self) -> str:
        return self._embedding_backend

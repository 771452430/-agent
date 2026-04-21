"""RAG embedding 全局设置的 SQLite 存储。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..schemas import RAGEmbeddingRuntimeSettings


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RAGEmbeddingSettingsStore:
    """单例 RAG embedding 配置存储。"""

    def __init__(self, sqlite_path: Path) -> None:
        self.sqlite_path = sqlite_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _table_columns(self, conn: sqlite3.Connection, table_name: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {row["name"] for row in rows}

    def _ensure_column(self, conn: sqlite3.Connection, table_name: str, column_name: str, ddl: str) -> None:
        if column_name not in self._table_columns(conn, table_name):
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rag_embedding_settings (
                    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                    provider_id TEXT,
                    model TEXT,
                    timeout_seconds INTEGER NOT NULL DEFAULT 20,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column(conn, "rag_embedding_settings", "provider_id", "TEXT")
            self._ensure_column(conn, "rag_embedding_settings", "model", "TEXT")
            self._ensure_column(conn, "rag_embedding_settings", "timeout_seconds", "INTEGER NOT NULL DEFAULT 20")

    def _row_to_runtime(self, row: sqlite3.Row) -> RAGEmbeddingRuntimeSettings:
        return RAGEmbeddingRuntimeSettings(
            provider_id=(row["provider_id"] or "").strip() or None,
            model=(row["model"] or "").strip() or None,
            timeout_seconds=max(5, int(row["timeout_seconds"] or 20)),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def get_runtime_settings(self) -> RAGEmbeddingRuntimeSettings | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM rag_embedding_settings WHERE singleton_id = 1").fetchone()
        return self._row_to_runtime(row) if row is not None else None

    def save_runtime_settings(self, runtime: RAGEmbeddingRuntimeSettings) -> RAGEmbeddingRuntimeSettings:
        current = self.get_runtime_settings()
        created_at = current.created_at if current is not None else runtime.created_at
        updated_at = runtime.updated_at
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO rag_embedding_settings (
                    singleton_id, provider_id, model, timeout_seconds, created_at, updated_at
                )
                VALUES (1, ?, ?, ?, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    provider_id = excluded.provider_id,
                    model = excluded.model,
                    timeout_seconds = excluded.timeout_seconds,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at
                """,
                (
                    runtime.provider_id,
                    runtime.model,
                    max(5, int(runtime.timeout_seconds)),
                    created_at.isoformat(),
                    updated_at.isoformat(),
                ),
            )
        saved = self.get_runtime_settings()
        assert saved is not None
        return saved

    def blank_runtime(self, *, timeout_seconds: int = 20) -> RAGEmbeddingRuntimeSettings:
        now = _utc_now()
        return RAGEmbeddingRuntimeSettings(
            provider_id=None,
            model=None,
            timeout_seconds=max(5, int(timeout_seconds)),
            created_at=now,
            updated_at=now,
        )

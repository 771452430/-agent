"""全局飞书设置的 SQLite 存储。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..schemas import FeishuRuntimeSettings


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FeishuSettingsStore:
    """单例飞书配置存储。"""

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
                CREATE TABLE IF NOT EXISTS feishu_settings (
                    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                    app_id TEXT,
                    app_secret TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column(conn, "feishu_settings", "app_id", "TEXT")
            self._ensure_column(conn, "feishu_settings", "app_secret", "TEXT")

    def _row_to_runtime(self, row: sqlite3.Row) -> FeishuRuntimeSettings:
        return FeishuRuntimeSettings(
            app_id=(row["app_id"] or "").strip() or None,
            app_secret=(row["app_secret"] or "").strip() or None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def get_runtime_settings(self) -> FeishuRuntimeSettings | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM feishu_settings WHERE singleton_id = 1").fetchone()
        return self._row_to_runtime(row) if row is not None else None

    def save_runtime_settings(self, runtime: FeishuRuntimeSettings) -> FeishuRuntimeSettings:
        current = self.get_runtime_settings()
        created_at = current.created_at if current is not None else runtime.created_at
        updated_at = runtime.updated_at
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO feishu_settings (
                    singleton_id, app_id, app_secret, created_at, updated_at
                )
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    app_id = excluded.app_id,
                    app_secret = excluded.app_secret,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at
                """,
                (
                    runtime.app_id,
                    runtime.app_secret,
                    created_at.isoformat(),
                    updated_at.isoformat(),
                ),
            )
        saved = self.get_runtime_settings()
        assert saved is not None
        return saved

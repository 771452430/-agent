"""GitLab 导入设置的 SQLite 存储。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..schemas import GitLabImportStoredSettings


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class GitLabSettingsStore:
    """单例 GitLab 导入配置存储。"""

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
                CREATE TABLE IF NOT EXISTS gitlab_import_settings (
                    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                    token TEXT,
                    allowed_hosts_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column(conn, "gitlab_import_settings", "token", "TEXT")
            self._ensure_column(conn, "gitlab_import_settings", "allowed_hosts_json", "TEXT")

    def _row_to_stored(self, row: sqlite3.Row) -> GitLabImportStoredSettings:
        raw_allowed_hosts = (row["allowed_hosts_json"] or "").strip()
        allowed_hosts: list[str] | None = None
        if raw_allowed_hosts != "":
            try:
                payload = json.loads(raw_allowed_hosts)
            except json.JSONDecodeError:
                payload = []
            if isinstance(payload, list):
                normalized = [str(item).strip() for item in payload if str(item).strip() != ""]
                allowed_hosts = normalized or None
        return GitLabImportStoredSettings(
            token=(row["token"] or "").strip() or None,
            allowed_hosts=allowed_hosts,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def get_stored_settings(self) -> GitLabImportStoredSettings | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM gitlab_import_settings WHERE singleton_id = 1").fetchone()
        return self._row_to_stored(row) if row is not None else None

    def save_stored_settings(self, stored: GitLabImportStoredSettings) -> GitLabImportStoredSettings:
        current = self.get_stored_settings()
        created_at = current.created_at if current is not None else stored.created_at
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO gitlab_import_settings (
                    singleton_id, token, allowed_hosts_json, created_at, updated_at
                )
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    token = excluded.token,
                    allowed_hosts_json = excluded.allowed_hosts_json,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at
                """,
                (
                    stored.token,
                    json.dumps(stored.allowed_hosts or [], ensure_ascii=False),
                    created_at.isoformat(),
                    stored.updated_at.isoformat(),
                ),
            )
        saved = self.get_stored_settings()
        assert saved is not None
        return saved

    def build_blank_settings(self) -> GitLabImportStoredSettings:
        now = _utc_now()
        return GitLabImportStoredSettings(token=None, allowed_hosts=None, created_at=now, updated_at=now)

"""Jira 历史数据同步设置与运行记录存储。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..schemas import JiraDataSourceRuntimeSettings, JiraDataSyncRun


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JiraDataSourceStore:
    """保存 Jira 数据源配置和最近同步结果。"""

    def __init__(self, sqlite_path: Path) -> None:
        self.sqlite_path = sqlite_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_column(self, conn: sqlite3.Connection, table_name: str, column_name: str, ddl: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
        if column_name not in columns:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jira_data_source_settings (
                    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                    enabled INTEGER NOT NULL,
                    db_path TEXT NOT NULL,
                    app_key TEXT,
                    app_secret TEXT,
                    sync_keyword TEXT NOT NULL,
                    sync_date_range TEXT NOT NULL,
                    sync_interval_minutes INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jira_data_sync_runs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    keyword TEXT NOT NULL,
                    date_range TEXT NOT NULL,
                    db_path TEXT NOT NULL,
                    matched_count INTEGER NOT NULL,
                    fetched_count INTEGER NOT NULL,
                    inserted_count INTEGER NOT NULL,
                    deleted_count INTEGER NOT NULL,
                    reindexed_count INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    error_message TEXT
                )
                """
            )
            self._ensure_column(conn, "jira_data_source_settings", "enabled", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "jira_data_source_settings", "db_path", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "jira_data_source_settings", "app_key", "TEXT")
            self._ensure_column(conn, "jira_data_source_settings", "app_secret", "TEXT")
            self._ensure_column(conn, "jira_data_source_settings", "sync_keyword", "TEXT NOT NULL DEFAULT '工作台'")
            self._ensure_column(conn, "jira_data_source_settings", "sync_date_range", "TEXT NOT NULL DEFAULT '本年'")
            self._ensure_column(conn, "jira_data_source_settings", "sync_interval_minutes", "INTEGER NOT NULL DEFAULT 1440")

    def _row_to_runtime(self, row: sqlite3.Row) -> JiraDataSourceRuntimeSettings:
        return JiraDataSourceRuntimeSettings(
            enabled=bool(row["enabled"]),
            db_path=row["db_path"] or "",
            app_key=(row["app_key"] or "").strip() or None,
            app_secret=(row["app_secret"] or "").strip() or None,
            sync_keyword=row["sync_keyword"] or "工作台",
            sync_date_range=row["sync_date_range"] or "本年",
            sync_interval_minutes=int(row["sync_interval_minutes"] or 1440),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _row_to_run(self, row: sqlite3.Row) -> JiraDataSyncRun:
        return JiraDataSyncRun(
            id=row["id"],
            status=row["status"],
            started_at=datetime.fromisoformat(row["started_at"]),
            ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
            keyword=row["keyword"],
            date_range=row["date_range"],
            db_path=row["db_path"],
            matched_count=int(row["matched_count"] or 0),
            fetched_count=int(row["fetched_count"] or 0),
            inserted_count=int(row["inserted_count"] or 0),
            deleted_count=int(row["deleted_count"] or 0),
            reindexed_count=int(row["reindexed_count"] or 0),
            summary=row["summary"] or "",
            error_message=row["error_message"],
        )

    def get_runtime_settings(self) -> JiraDataSourceRuntimeSettings | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jira_data_source_settings WHERE singleton_id = 1").fetchone()
        return self._row_to_runtime(row) if row is not None else None

    def save_runtime_settings(self, runtime: JiraDataSourceRuntimeSettings) -> JiraDataSourceRuntimeSettings:
        current = self.get_runtime_settings()
        created_at = current.created_at if current is not None else runtime.created_at
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jira_data_source_settings (
                    singleton_id, enabled, db_path, app_key, app_secret, sync_keyword,
                    sync_date_range, sync_interval_minutes, created_at, updated_at
                )
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    db_path = excluded.db_path,
                    app_key = excluded.app_key,
                    app_secret = excluded.app_secret,
                    sync_keyword = excluded.sync_keyword,
                    sync_date_range = excluded.sync_date_range,
                    sync_interval_minutes = excluded.sync_interval_minutes,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at
                """,
                (
                    1 if runtime.enabled else 0,
                    runtime.db_path,
                    runtime.app_key,
                    runtime.app_secret,
                    runtime.sync_keyword,
                    runtime.sync_date_range,
                    runtime.sync_interval_minutes,
                    created_at.isoformat(),
                    runtime.updated_at.isoformat(),
                ),
            )
        saved = self.get_runtime_settings()
        assert saved is not None
        return saved

    def create_run(self, *, keyword: str, date_range: str, db_path: str) -> JiraDataSyncRun:
        now = _utc_now()
        run = JiraDataSyncRun(
            id=str(uuid4()),
            status="running",
            started_at=now,
            keyword=keyword,
            date_range=date_range,
            db_path=db_path,
            summary="同步运行中",
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jira_data_sync_runs (
                    id, status, started_at, ended_at, keyword, date_range, db_path,
                    matched_count, fetched_count, inserted_count, deleted_count, reindexed_count,
                    summary, error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.status,
                    run.started_at.isoformat(),
                    None,
                    run.keyword,
                    run.date_range,
                    run.db_path,
                    0,
                    0,
                    0,
                    0,
                    0,
                    run.summary,
                    None,
                ),
            )
        return run

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        matched_count: int,
        fetched_count: int,
        inserted_count: int,
        deleted_count: int,
        reindexed_count: int,
        summary: str,
        error_message: str | None = None,
    ) -> JiraDataSyncRun:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jira_data_sync_runs
                SET status = ?, ended_at = ?, matched_count = ?, fetched_count = ?,
                    inserted_count = ?, deleted_count = ?, reindexed_count = ?,
                    summary = ?, error_message = ?
                WHERE id = ?
                """,
                (
                    status,
                    now.isoformat(),
                    matched_count,
                    fetched_count,
                    inserted_count,
                    deleted_count,
                    reindexed_count,
                    summary,
                    error_message,
                    run_id,
                ),
            )
            row = conn.execute("SELECT * FROM jira_data_sync_runs WHERE id = ?", (run_id,)).fetchone()
        assert row is not None
        return self._row_to_run(row)

    def list_runs(self, limit: int = 20) -> list[JiraDataSyncRun]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jira_data_sync_runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def latest_run(self) -> JiraDataSyncRun | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jira_data_sync_runs ORDER BY started_at DESC LIMIT 1").fetchone()
        return self._row_to_run(row) if row is not None else None

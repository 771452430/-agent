"""全局邮箱设置的 SQLite 存储。

这里的职责只包含“持久化”：
- 保存一套全局 SMTP 配置；
- 读取时返回完整运行时配置；
- 不处理环境变量 fallback，也不直接发邮件。

这样拆开的好处是：
- store 只关心数据库；
- service 只关心“最终有效配置”和 SMTP 行为；
- watcher 只关心“我要不要发通知”。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..schemas import MailRuntimeSettings


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MailSettingsStore:
    """单例邮箱配置存储。"""

    def __init__(self, sqlite_path: Path) -> None:
        self.sqlite_path = sqlite_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mail_settings (
                    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                    enabled INTEGER NOT NULL,
                    smtp_host TEXT NOT NULL,
                    smtp_port INTEGER NOT NULL,
                    smtp_username TEXT NOT NULL,
                    smtp_password TEXT,
                    use_tls INTEGER NOT NULL,
                    use_ssl INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _row_to_runtime(self, row: sqlite3.Row) -> MailRuntimeSettings:
        smtp_username = row["smtp_username"] or ""
        return MailRuntimeSettings(
            enabled=bool(row["enabled"]),
            smtp_host=row["smtp_host"] or "",
            smtp_port=int(row["smtp_port"] or 587),
            smtp_username=smtp_username,
            smtp_password=row["smtp_password"] or None,
            use_tls=bool(row["use_tls"]),
            use_ssl=bool(row["use_ssl"]),
            sender_email=smtp_username,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def get_runtime_settings(self) -> MailRuntimeSettings | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM mail_settings WHERE singleton_id = 1").fetchone()
        return self._row_to_runtime(row) if row is not None else None

    def save_runtime_settings(self, runtime: MailRuntimeSettings) -> MailRuntimeSettings:
        current = self.get_runtime_settings()
        created_at = current.created_at if current is not None else runtime.created_at
        updated_at = runtime.updated_at
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mail_settings (
                    singleton_id, enabled, smtp_host, smtp_port, smtp_username,
                    smtp_password, use_tls, use_ssl, created_at, updated_at
                )
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    smtp_host = excluded.smtp_host,
                    smtp_port = excluded.smtp_port,
                    smtp_username = excluded.smtp_username,
                    smtp_password = excluded.smtp_password,
                    use_tls = excluded.use_tls,
                    use_ssl = excluded.use_ssl,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at
                """,
                (
                    1 if runtime.enabled else 0,
                    runtime.smtp_host,
                    runtime.smtp_port,
                    runtime.smtp_username,
                    runtime.smtp_password,
                    1 if runtime.use_tls else 0,
                    1 if runtime.use_ssl else 0,
                    created_at.isoformat(),
                    updated_at.isoformat(),
                ),
            )
        saved = self.get_runtime_settings()
        assert saved is not None
        return saved

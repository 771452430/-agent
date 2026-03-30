"""巡检 Agent 的持久化存储。

这个 store 同时管理三类状态：
1. `watcher_agents`：巡检 Agent 配置；
2. `watcher_runs`：每次轮巡/手动运行的结果；
3. `watcher_seen_bugs`：已经见过的 bug_id，用来实现“只看新增”。

之所以把这三类状态集中放在一个模块里，是因为巡检 Agent 的核心并不是一次性的
LLM 调用，而是“长期状态 + 周期执行 + 增量比较”。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from ..schemas import (
    CreateWatcherRequest,
    ModelConfig,
    OwnerRule,
    ParsedBug,
    SeenBugRecord,
    UpdateWatcherRequest,
    WatcherAgentConfig,
    WatcherAssignmentResult,
    WatcherRun,
)

# _ 表示：
# 👉 内部函数（不建议外部调用）
# 个函数返回 datetime 类型
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


SCHEDULED_SUCCESS_STATUSES = {"success", "no_change", "baseline_seeded"}
SCHEDULED_FAILURE_STATUSES = {"failed", "partial_success"}


class WatcherStore:
    """负责巡检 Agent 配置、运行记录和已见 Bug 记录。"""

    def __init__(self, sqlite_path: Path) -> None:
        self.sqlite_path = sqlite_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS watcher_agents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    dashboard_url TEXT NOT NULL,
                    request_method TEXT NOT NULL DEFAULT 'GET',
                    request_headers_json TEXT NOT NULL,
                    request_body_json TEXT NOT NULL DEFAULT 'null',
                    poll_interval_minutes INTEGER NOT NULL,
                    sender_email TEXT NOT NULL,
                    recipient_emails_json TEXT NOT NULL,
                    model_config_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    owner_rules_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_run_at TEXT,
                    next_run_at TEXT,
                    consecutive_failure_count INTEGER NOT NULL DEFAULT 0,
                    auto_disabled_at TEXT,
                    auto_disabled_reason TEXT
                );

                CREATE TABLE IF NOT EXISTS watcher_runs (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    fetched_count INTEGER NOT NULL,
                    parsed_count INTEGER NOT NULL,
                    new_bug_count INTEGER NOT NULL,
                    assigned_count INTEGER NOT NULL,
                    emailed INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    error_message TEXT,
                    assignment_results_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS watcher_seen_bugs (
                    agent_id TEXT NOT NULL,
                    bug_id TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    latest_title TEXT NOT NULL,
                    latest_service TEXT NOT NULL,
                    latest_module TEXT NOT NULL,
                    latest_status TEXT NOT NULL,
                    PRIMARY KEY (agent_id, bug_id)
                );
                """
            )
            self._ensure_column(conn, "watcher_agents", "request_method", "TEXT NOT NULL DEFAULT 'GET'")
            self._ensure_column(conn, "watcher_agents", "request_body_json", "TEXT NOT NULL DEFAULT 'null'")
            self._ensure_column(conn, "watcher_agents", "consecutive_failure_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "watcher_agents", "auto_disabled_at", "TEXT")
            self._ensure_column(conn, "watcher_agents", "auto_disabled_reason", "TEXT")

    def _ensure_column(self, conn: sqlite3.Connection, table_name: str, column_name: str, ddl: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
        if column_name in columns:
            return
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")

    def _parse_owner_rules(self, raw: str | None) -> list[OwnerRule]:
        items = json.loads(raw or "[]")
        return [OwnerRule.model_validate(item) for item in items]

    def _parse_assignment_results(self, raw: str | None) -> list[WatcherAssignmentResult]:
        items = json.loads(raw or "[]")
        return [WatcherAssignmentResult.model_validate(item) for item in items]

    def _row_to_config(self, row: sqlite3.Row) -> WatcherAgentConfig:
        return WatcherAgentConfig(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            dashboard_url=row["dashboard_url"],
            request_method=row["request_method"] or "GET",
            request_headers=json.loads(row["request_headers_json"] or "{}"),
            request_body_json=json.loads(row["request_body_json"] or "null"),
            poll_interval_minutes=int(row["poll_interval_minutes"]),
            sender_email=row["sender_email"] or "",
            recipient_emails=json.loads(row["recipient_emails_json"] or "[]"),
            model_settings=ModelConfig.model_validate_json(row["model_config_json"]),
            enabled=bool(row["enabled"]),
            owner_rules=self._parse_owner_rules(row["owner_rules_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            last_run_at=datetime.fromisoformat(row["last_run_at"]) if row["last_run_at"] else None,
            next_run_at=datetime.fromisoformat(row["next_run_at"]) if row["next_run_at"] else None,
            last_run_status=row["last_run_status"],
            last_new_bug_count=int(row["last_new_bug_count"] or 0),
            last_emailed=bool(row["last_emailed"]) if row["last_emailed"] is not None else None,
            consecutive_failure_count=int(row["consecutive_failure_count"] or 0),
            auto_disabled_at=datetime.fromisoformat(row["auto_disabled_at"]) if row["auto_disabled_at"] else None,
            auto_disabled_reason=row["auto_disabled_reason"],
        )

    def _row_to_run(self, row: sqlite3.Row) -> WatcherRun:
        return WatcherRun(
            id=row["id"],
            agent_id=row["agent_id"],
            status=row["status"],
            started_at=datetime.fromisoformat(row["started_at"]),
            ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
            fetched_count=int(row["fetched_count"]),
            parsed_count=int(row["parsed_count"]),
            new_bug_count=int(row["new_bug_count"]),
            assigned_count=int(row["assigned_count"]),
            emailed=bool(row["emailed"]),
            summary=row["summary"] or "",
            error_message=row["error_message"],
            assignment_results=self._parse_assignment_results(row["assignment_results_json"]),
        )

    def _select_agents_query(self) -> str:
        return """
            SELECT
                a.*,
                r.status AS last_run_status,
                r.new_bug_count AS last_new_bug_count,
                r.emailed AS last_emailed
            FROM watcher_agents a
            LEFT JOIN watcher_runs r
                ON r.id = (
                    SELECT id
                    FROM watcher_runs
                    WHERE agent_id = a.id
                    ORDER BY started_at DESC
                    LIMIT 1
                )
        """

    def list_watchers(self) -> list[WatcherAgentConfig]:
        with self._connect() as conn:
            rows = conn.execute(
                self._select_agents_query() + " ORDER BY COALESCE(a.last_run_at, a.updated_at) DESC, a.created_at DESC"
            ).fetchall()
        return [self._row_to_config(row) for row in rows]

    def get_watcher(self, watcher_id: str) -> WatcherAgentConfig | None:
        with self._connect() as conn:
            row = conn.execute(self._select_agents_query() + " WHERE a.id = ?", (watcher_id,)).fetchone()
        return self._row_to_config(row) if row is not None else None

    def create_watcher(self, request: CreateWatcherRequest, model_config: ModelConfig) -> WatcherAgentConfig:
        watcher_id = str(uuid4())
        now = _utc_now()
        next_run_at = now + timedelta(minutes=request.poll_interval_minutes) if request.enabled else None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO watcher_agents (
                    id, name, description, dashboard_url, request_method, request_headers_json, request_body_json,
                    poll_interval_minutes, sender_email, recipient_emails_json,
                    model_config_json, enabled, owner_rules_json,
                    created_at, updated_at, last_run_at, next_run_at,
                    consecutive_failure_count, auto_disabled_at, auto_disabled_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    watcher_id,
                    request.name,
                    request.description,
                    request.dashboard_url,
                    request.request_method,
                    json.dumps(request.request_headers, ensure_ascii=False),
                    json.dumps(request.request_body_json, ensure_ascii=False),
                    request.poll_interval_minutes,
                    request.sender_email,
                    json.dumps(request.recipient_emails, ensure_ascii=False),
                    model_config.model_dump_json(),
                    1 if request.enabled else 0,
                    json.dumps([rule.model_dump(mode="json") for rule in request.owner_rules], ensure_ascii=False),
                    now.isoformat(),
                    now.isoformat(),
                    None,
                    next_run_at.isoformat() if next_run_at else None,
                    0,
                    None,
                    None,
                ),
            )
        watcher = self.get_watcher(watcher_id)
        assert watcher is not None
        return watcher

    def update_watcher(
        self,
        watcher_id: str,
        request: UpdateWatcherRequest,
        model_config: ModelConfig | None = None,
    ) -> WatcherAgentConfig | None:
        current = self.get_watcher(watcher_id)
        if current is None:
            return None

        now = _utc_now()
        next_enabled = current.enabled if request.enabled is None else request.enabled
        next_poll_interval = current.poll_interval_minutes if request.poll_interval_minutes is None else request.poll_interval_minutes
        next_next_run_at = current.next_run_at
        if not next_enabled:
            next_next_run_at = None
        elif request.enabled is True and not current.enabled:
            next_next_run_at = now + timedelta(minutes=next_poll_interval)
        elif request.poll_interval_minutes is not None:
            next_next_run_at = now + timedelta(minutes=next_poll_interval)
        elif next_next_run_at is None:
            next_next_run_at = now + timedelta(minutes=next_poll_interval)
        next_consecutive_failure_count = current.consecutive_failure_count
        next_auto_disabled_at = current.auto_disabled_at
        next_auto_disabled_reason = current.auto_disabled_reason
        if request.enabled is True and not current.enabled:
            next_consecutive_failure_count = 0
            next_auto_disabled_at = None
            next_auto_disabled_reason = None
        next_request_method = request.request_method if request.request_method is not None else current.request_method
        if next_request_method == "GET":
            next_request_body_json = None
        elif request.request_body_json is not None:
            next_request_body_json = request.request_body_json
        else:
            next_request_body_json = current.request_body_json

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE watcher_agents
                SET name = ?, description = ?, dashboard_url = ?, request_method = ?, request_headers_json = ?, request_body_json = ?,
                    poll_interval_minutes = ?, sender_email = ?, recipient_emails_json = ?,
                    model_config_json = ?, enabled = ?, owner_rules_json = ?, updated_at = ?, next_run_at = ?,
                    consecutive_failure_count = ?, auto_disabled_at = ?, auto_disabled_reason = ?
                WHERE id = ?
                """,
                (
                    request.name if request.name is not None else current.name,
                    request.description if request.description is not None else current.description,
                    request.dashboard_url if request.dashboard_url is not None else current.dashboard_url,
                    next_request_method,
                    json.dumps(
                        request.request_headers if request.request_headers is not None else current.request_headers,
                        ensure_ascii=False,
                    ),
                    json.dumps(next_request_body_json, ensure_ascii=False),
                    next_poll_interval,
                    request.sender_email if request.sender_email is not None else current.sender_email,
                    json.dumps(
                        request.recipient_emails if request.recipient_emails is not None else current.recipient_emails,
                        ensure_ascii=False,
                    ),
                    (model_config or current.model_settings).model_dump_json(),
                    1 if next_enabled else 0,
                    json.dumps(
                        [
                            rule.model_dump(mode="json")
                            for rule in (
                                request.owner_rules if request.owner_rules is not None else current.owner_rules
                            )
                        ],
                        ensure_ascii=False,
                    ),
                    now.isoformat(),
                    next_next_run_at.isoformat() if next_next_run_at else None,
                    next_consecutive_failure_count,
                    next_auto_disabled_at.isoformat() if next_auto_disabled_at else None,
                    next_auto_disabled_reason,
                    watcher_id,
                ),
            )
        return self.get_watcher(watcher_id)

    def list_due_watchers(self, current_time: datetime | None = None) -> list[WatcherAgentConfig]:
        now = (current_time or _utc_now()).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                self._select_agents_query()
                + """
                WHERE a.enabled = 1
                  AND a.next_run_at IS NOT NULL
                  AND a.next_run_at <= ?
                ORDER BY a.next_run_at ASC
                """,
                (now,),
            ).fetchall()
        return [self._row_to_config(row) for row in rows]

    def list_runs(self, watcher_id: str) -> list[WatcherRun]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM watcher_runs WHERE agent_id = ? ORDER BY started_at DESC",
                (watcher_id,),
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def list_seen_bug_records(self, watcher_id: str) -> list[SeenBugRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM watcher_seen_bugs
                WHERE agent_id = ?
                ORDER BY first_seen_at ASC
                """,
                (watcher_id,),
            ).fetchall()
        return [
            SeenBugRecord(
                agent_id=row["agent_id"],
                bug_id=row["bug_id"],
                first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
                latest_title=row["latest_title"] or "",
                latest_service=row["latest_service"] or "",
                latest_module=row["latest_module"] or "",
                latest_status=row["latest_status"] or "",
            )
            for row in rows
        ]

    def get_seen_bug_ids(self, watcher_id: str) -> set[str]:
        return {item.bug_id for item in self.list_seen_bug_records(watcher_id)}

    def upsert_seen_bugs(self, watcher_id: str, bugs: list[ParsedBug], seen_at: datetime | None = None) -> None:
        seen_time = (seen_at or _utc_now()).isoformat()
        rows = [
            (
                watcher_id,
                bug.bug_id,
                seen_time,
                bug.title,
                bug.service,
                bug.module,
                bug.status,
            )
            for bug in bugs
        ]
        if len(rows) == 0:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO watcher_seen_bugs (
                    agent_id, bug_id, first_seen_at, latest_title, latest_service, latest_module, latest_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id, bug_id) DO UPDATE SET
                    latest_title = excluded.latest_title,
                    latest_service = excluded.latest_service,
                    latest_module = excluded.latest_module,
                    latest_status = excluded.latest_status
                """,
                rows,
            )

    def record_run(self, watcher_id: str, run: WatcherRun) -> None:
        watcher = self.get_watcher(watcher_id)
        if watcher is None:
            raise ValueError(f"Watcher not found: {watcher_id}")

        ended_at = run.ended_at or _utc_now()
        next_run_at = ended_at + timedelta(minutes=watcher.poll_interval_minutes) if watcher.enabled else None

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO watcher_runs (
                    id, agent_id, status, started_at, ended_at, fetched_count,
                    parsed_count, new_bug_count, assigned_count, emailed, summary,
                    error_message, assignment_results_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    watcher_id,
                    run.status,
                    run.started_at.isoformat(),
                    ended_at.isoformat(),
                    run.fetched_count,
                    run.parsed_count,
                    run.new_bug_count,
                    run.assigned_count,
                    1 if run.emailed else 0,
                    run.summary,
                    run.error_message,
                    json.dumps([item.model_dump(mode="json") for item in run.assignment_results], ensure_ascii=False),
                ),
            )
            conn.execute(
                """
                UPDATE watcher_agents
                SET last_run_at = ?, next_run_at = ?
                WHERE id = ?
                """,
                (ended_at.isoformat(), next_run_at.isoformat() if next_run_at else None, watcher_id),
            )

    def update_run_summary(self, run_id: str, summary: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE watcher_runs SET summary = ? WHERE id = ?", (summary, run_id))

    def apply_scheduled_run_policy(
        self,
        watcher_id: str,
        run: WatcherRun,
        *,
        failure_threshold: int = 2,
    ) -> dict[str, object]:
        watcher = self.get_watcher(watcher_id)
        if watcher is None:
            raise ValueError(f"Watcher not found: {watcher_id}")

        if run.status not in SCHEDULED_SUCCESS_STATUSES | SCHEDULED_FAILURE_STATUSES:
            return {
                "consecutive_failure_count": watcher.consecutive_failure_count,
                "auto_disabled": False,
                "auto_disabled_at": watcher.auto_disabled_at,
                "auto_disabled_reason": watcher.auto_disabled_reason,
                "summary": run.summary,
            }

        now = _utc_now()
        next_failure_count = 0
        auto_disabled = False
        auto_disabled_at = watcher.auto_disabled_at
        auto_disabled_reason = watcher.auto_disabled_reason
        summary = run.summary

        if run.status in SCHEDULED_SUCCESS_STATUSES:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE watcher_agents
                    SET consecutive_failure_count = 0,
                        auto_disabled_at = NULL,
                        auto_disabled_reason = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now.isoformat(), watcher_id),
                )
            return {
                "consecutive_failure_count": 0,
                "auto_disabled": False,
                "auto_disabled_at": None,
                "auto_disabled_reason": None,
                "summary": summary,
            }

        next_failure_count = watcher.consecutive_failure_count + 1
        failure_reason = (run.error_message or run.summary or "轮巡失败").strip()

        if next_failure_count >= failure_threshold and watcher.enabled:
            auto_disabled = True
            auto_disabled_at = run.ended_at or now
            auto_disabled_reason = failure_reason
            summary = f"{run.summary} 连续失败 {next_failure_count}/{failure_threshold}，已自动停用轮巡。".strip()
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE watcher_agents
                    SET enabled = 0,
                        next_run_at = NULL,
                        consecutive_failure_count = ?,
                        auto_disabled_at = ?,
                        auto_disabled_reason = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        next_failure_count,
                        auto_disabled_at.isoformat(),
                        auto_disabled_reason,
                        now.isoformat(),
                        watcher_id,
                    ),
                )
        else:
            summary = f"{run.summary} 连续失败 {next_failure_count}/{failure_threshold}。".strip()
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE watcher_agents
                    SET consecutive_failure_count = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (next_failure_count, now.isoformat(), watcher_id),
                )

        self.update_run_summary(run.id, summary)
        return {
            "consecutive_failure_count": next_failure_count,
            "auto_disabled": auto_disabled,
            "auto_disabled_at": auto_disabled_at,
            "auto_disabled_reason": auto_disabled_reason,
            "summary": summary,
        }

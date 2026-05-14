"""Jira 重复工单审核 Agent 的持久化存储。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..schemas import (
    CreateJiraDuplicateAgentRequest,
    JiraDuplicateAgentConfig,
    JiraDuplicateIssueResult,
    JiraDuplicateRun,
    ModelConfig,
    UpdateJiraDuplicateAgentRequest,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_json_dict(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _load_issue_results(raw: str | None) -> list[JiraDuplicateIssueResult]:
    try:
        value = json.loads(raw or "[]")
    except Exception:
        return []
    if not isinstance(value, list):
        return []
    return [JiraDuplicateIssueResult.model_validate(item) for item in value]


class JiraDuplicateStore:
    """管理 Jira 重复工单 Agent 配置、运行记录和本地案例索引。"""

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

    def _ensure_fts_table(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS jira_duplicate_case_fts
                USING fts5(issue_key UNINDEXED, search_text, tokenize='trigram')
                """
            )
        except sqlite3.OperationalError:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS jira_duplicate_case_fts
                USING fts5(issue_key UNINDEXED, search_text, tokenize='unicode61')
                """
            )

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jira_duplicate_agents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    source_db_path TEXT NOT NULL,
                    dashboard_url TEXT NOT NULL,
                    request_method TEXT NOT NULL DEFAULT 'GET',
                    request_headers_json TEXT NOT NULL DEFAULT '{}',
                    request_body_json TEXT NOT NULL DEFAULT 'null',
                    request_body_text TEXT,
                    detail_url_template TEXT,
                    detail_request_method TEXT NOT NULL DEFAULT 'GET',
                    detail_request_headers_json TEXT NOT NULL DEFAULT '{}',
                    detail_request_body_text TEXT,
                    poll_interval_minutes INTEGER NOT NULL,
                    high_similarity_threshold REAL NOT NULL DEFAULT 0.78,
                    medium_similarity_threshold REAL NOT NULL DEFAULT 0.55,
                    model_review_enabled INTEGER NOT NULL DEFAULT 0,
                    model_config_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_run_at TEXT,
                    next_run_at TEXT
                );

                CREATE TABLE IF NOT EXISTS jira_duplicate_runs (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    fetched_count INTEGER NOT NULL,
                    parsed_count INTEGER NOT NULL,
                    matched_count INTEGER NOT NULL,
                    high_confidence_count INTEGER NOT NULL,
                    medium_confidence_count INTEGER NOT NULL,
                    no_match_count INTEGER NOT NULL,
                    failed_count INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    error_message TEXT,
                    issue_results_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jira_duplicate_case_index (
                    issue_key TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    description TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    module TEXT NOT NULL,
                    status TEXT NOT NULL,
                    solution TEXT NOT NULL,
                    versions TEXT NOT NULL,
                    fix_versions TEXT NOT NULL,
                    updated TEXT NOT NULL,
                    search_text TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jira_duplicate_index_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._ensure_column(conn, "jira_duplicate_agents", "source_db_path", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "jira_duplicate_agents", "high_similarity_threshold", "REAL NOT NULL DEFAULT 0.78")
            self._ensure_column(conn, "jira_duplicate_agents", "medium_similarity_threshold", "REAL NOT NULL DEFAULT 0.55")
            self._ensure_column(conn, "jira_duplicate_agents", "model_review_enabled", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_fts_table(conn)

    def _row_to_config(self, row: sqlite3.Row) -> JiraDuplicateAgentConfig:
        return JiraDuplicateAgentConfig(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            source_db_path=row["source_db_path"] or "",
            dashboard_url=row["dashboard_url"],
            request_method=row["request_method"] or "GET",
            request_headers=_load_json_dict(row["request_headers_json"]),
            request_body_json=json.loads(row["request_body_json"] or "null"),
            request_body_text=row["request_body_text"],
            detail_url_template=row["detail_url_template"],
            detail_request_method=row["detail_request_method"] or "GET",
            detail_request_headers=_load_json_dict(row["detail_request_headers_json"]),
            detail_request_body_text=row["detail_request_body_text"],
            poll_interval_minutes=int(row["poll_interval_minutes"]),
            high_similarity_threshold=float(row["high_similarity_threshold"] or 0.78),
            medium_similarity_threshold=float(row["medium_similarity_threshold"] or 0.55),
            model_review_enabled=bool(row["model_review_enabled"]),
            model_settings=ModelConfig.model_validate_json(row["model_config_json"]),
            enabled=bool(row["enabled"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            last_run_at=datetime.fromisoformat(row["last_run_at"]) if row["last_run_at"] else None,
            next_run_at=datetime.fromisoformat(row["next_run_at"]) if row["next_run_at"] else None,
            last_run_status=row["last_run_status"],
            last_matched_count=int(row["last_matched_count"] or 0),
        )

    def _row_to_run(self, row: sqlite3.Row) -> JiraDuplicateRun:
        return JiraDuplicateRun(
            id=row["id"],
            agent_id=row["agent_id"],
            status=row["status"],
            started_at=datetime.fromisoformat(row["started_at"]),
            ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
            fetched_count=int(row["fetched_count"]),
            parsed_count=int(row["parsed_count"]),
            matched_count=int(row["matched_count"]),
            high_confidence_count=int(row["high_confidence_count"]),
            medium_confidence_count=int(row["medium_confidence_count"]),
            no_match_count=int(row["no_match_count"]),
            failed_count=int(row["failed_count"]),
            summary=row["summary"] or "",
            error_message=row["error_message"],
            issue_results=_load_issue_results(row["issue_results_json"]),
        )

    def _select_agents_query(self) -> str:
        return """
            SELECT
                a.*,
                r.status AS last_run_status,
                r.matched_count AS last_matched_count
            FROM jira_duplicate_agents a
            LEFT JOIN jira_duplicate_runs r
                ON r.id = (
                    SELECT id
                    FROM jira_duplicate_runs
                    WHERE agent_id = a.id
                    ORDER BY started_at DESC
                    LIMIT 1
                )
        """

    def list_agents(self) -> list[JiraDuplicateAgentConfig]:
        with self._connect() as conn:
            rows = conn.execute(
                self._select_agents_query()
                + " ORDER BY COALESCE(a.last_run_at, a.updated_at) DESC, a.created_at DESC"
            ).fetchall()
        return [self._row_to_config(row) for row in rows]

    def get_agent(self, agent_id: str) -> JiraDuplicateAgentConfig | None:
        with self._connect() as conn:
            row = conn.execute(self._select_agents_query() + " WHERE a.id = ?", (agent_id,)).fetchone()
        return self._row_to_config(row) if row is not None else None

    def create_agent(
        self,
        request: CreateJiraDuplicateAgentRequest,
        model_config: ModelConfig,
    ) -> JiraDuplicateAgentConfig:
        agent_id = str(uuid4())
        now = _utc_now()
        next_run_at = now + timedelta(minutes=request.poll_interval_minutes) if request.enabled else None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jira_duplicate_agents (
                    id, name, description, source_db_path, dashboard_url, request_method, request_headers_json,
                    request_body_json, request_body_text, detail_url_template, detail_request_method,
                    detail_request_headers_json, detail_request_body_text, poll_interval_minutes,
                    high_similarity_threshold, medium_similarity_threshold, model_review_enabled, model_config_json,
                    enabled, created_at, updated_at, last_run_at, next_run_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_id,
                    request.name,
                    request.description,
                    request.source_db_path,
                    request.dashboard_url,
                    request.request_method,
                    json.dumps(request.request_headers, ensure_ascii=False),
                    json.dumps(request.request_body_json, ensure_ascii=False),
                    request.request_body_text.strip() if request.request_body_text else None,
                    request.detail_url_template.strip() if request.detail_url_template else None,
                    request.detail_request_method,
                    json.dumps(request.detail_request_headers, ensure_ascii=False),
                    request.detail_request_body_text.strip() if request.detail_request_body_text else None,
                    request.poll_interval_minutes,
                    request.high_similarity_threshold,
                    request.medium_similarity_threshold,
                    1 if request.model_review_enabled else 0,
                    model_config.model_dump_json(),
                    1 if request.enabled else 0,
                    now.isoformat(),
                    now.isoformat(),
                    None,
                    next_run_at.isoformat() if next_run_at else None,
                ),
            )
        agent = self.get_agent(agent_id)
        assert agent is not None
        return agent

    def update_agent(
        self,
        agent_id: str,
        request: UpdateJiraDuplicateAgentRequest,
        model_config: ModelConfig | None = None,
    ) -> JiraDuplicateAgentConfig | None:
        current = self.get_agent(agent_id)
        if current is None:
            return None

        now = _utc_now()
        next_enabled = current.enabled if request.enabled is None else request.enabled
        next_poll_interval = current.poll_interval_minutes if request.poll_interval_minutes is None else request.poll_interval_minutes
        next_run_at = current.next_run_at
        if not next_enabled:
            next_run_at = None
        elif request.enabled is True and not current.enabled:
            next_run_at = now + timedelta(minutes=next_poll_interval)
        elif request.poll_interval_minutes is not None or next_run_at is None:
            next_run_at = now + timedelta(minutes=next_poll_interval)

        next_request_method = request.request_method if request.request_method is not None else current.request_method
        if next_request_method == "GET":
            next_request_body_json = None
            next_request_body_text = None
        elif "request_body_json" in request.model_fields_set:
            next_request_body_json = request.request_body_json
            next_request_body_text = current.request_body_text
        elif "request_body_text" in request.model_fields_set:
            next_request_body_json = current.request_body_json
            next_request_body_text = request.request_body_text.strip() if request.request_body_text else None
        else:
            next_request_body_json = current.request_body_json
            next_request_body_text = current.request_body_text

        if "detail_url_template" in request.model_fields_set:
            next_detail_url_template = request.detail_url_template.strip() if request.detail_url_template else None
        else:
            next_detail_url_template = current.detail_url_template
        next_detail_request_method = (
            request.detail_request_method if request.detail_request_method is not None else current.detail_request_method
        )
        if next_detail_url_template is None:
            next_detail_request_method = "GET"
            next_detail_headers: dict[str, str] = {}
            next_detail_body = None
        else:
            next_detail_headers = request.detail_request_headers if request.detail_request_headers is not None else current.detail_request_headers
            if next_detail_request_method == "GET":
                next_detail_body = None
            elif "detail_request_body_text" in request.model_fields_set:
                next_detail_body = request.detail_request_body_text.strip() if request.detail_request_body_text else None
            else:
                next_detail_body = current.detail_request_body_text

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jira_duplicate_agents
                SET name = ?, description = ?, source_db_path = ?, dashboard_url = ?, request_method = ?,
                    request_headers_json = ?, request_body_json = ?, request_body_text = ?,
                    detail_url_template = ?, detail_request_method = ?, detail_request_headers_json = ?,
                    detail_request_body_text = ?, poll_interval_minutes = ?, high_similarity_threshold = ?,
                    medium_similarity_threshold = ?, model_review_enabled = ?, model_config_json = ?, enabled = ?, updated_at = ?,
                    next_run_at = ?
                WHERE id = ?
                """,
                (
                    request.name if request.name is not None else current.name,
                    request.description if request.description is not None else current.description,
                    request.source_db_path if request.source_db_path is not None else current.source_db_path,
                    request.dashboard_url if request.dashboard_url is not None else current.dashboard_url,
                    next_request_method,
                    json.dumps(
                        request.request_headers if request.request_headers is not None else current.request_headers,
                        ensure_ascii=False,
                    ),
                    json.dumps(next_request_body_json, ensure_ascii=False),
                    next_request_body_text,
                    next_detail_url_template,
                    next_detail_request_method,
                    json.dumps(next_detail_headers, ensure_ascii=False),
                    next_detail_body,
                    next_poll_interval,
                    (
                        request.high_similarity_threshold
                        if request.high_similarity_threshold is not None
                        else current.high_similarity_threshold
                    ),
                    (
                        request.medium_similarity_threshold
                        if request.medium_similarity_threshold is not None
                        else current.medium_similarity_threshold
                    ),
                    (
                        1 if request.model_review_enabled else 0
                    ) if request.model_review_enabled is not None else (1 if current.model_review_enabled else 0),
                    (model_config or current.model_settings).model_dump_json(),
                    1 if next_enabled else 0,
                    now.isoformat(),
                    next_run_at.isoformat() if next_run_at else None,
                    agent_id,
                ),
            )
        return self.get_agent(agent_id)

    def list_due_agents(self, current_time: datetime | None = None) -> list[JiraDuplicateAgentConfig]:
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

    def record_run(self, agent: JiraDuplicateAgentConfig, run: JiraDuplicateRun) -> JiraDuplicateRun:
        next_run_at = None
        if agent.enabled:
            next_run_at = (run.ended_at or _utc_now()) + timedelta(minutes=agent.poll_interval_minutes)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jira_duplicate_runs (
                    id, agent_id, status, started_at, ended_at, fetched_count, parsed_count, matched_count,
                    high_confidence_count, medium_confidence_count, no_match_count, failed_count,
                    summary, error_message, issue_results_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.agent_id,
                    run.status,
                    run.started_at.isoformat(),
                    run.ended_at.isoformat() if run.ended_at else None,
                    run.fetched_count,
                    run.parsed_count,
                    run.matched_count,
                    run.high_confidence_count,
                    run.medium_confidence_count,
                    run.no_match_count,
                    run.failed_count,
                    run.summary,
                    run.error_message,
                    json.dumps([item.model_dump(mode="json") for item in run.issue_results], ensure_ascii=False),
                ),
            )
            conn.execute(
                """
                UPDATE jira_duplicate_agents
                SET last_run_at = ?, next_run_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    (run.ended_at or _utc_now()).isoformat(),
                    next_run_at.isoformat() if next_run_at else None,
                    _utc_now().isoformat(),
                    agent.id,
                ),
            )
        return run

    def list_runs(self, agent_id: str) -> list[JiraDuplicateRun]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jira_duplicate_runs WHERE agent_id = ? ORDER BY started_at DESC",
                (agent_id,),
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def replace_case_index(
        self,
        cases: list[dict[str, str]],
        *,
        embedding_backend: str,
        normalizer_version: str,
        source_db_path: str = "",
    ) -> None:
        now = _utc_now().isoformat()
        with self._connect() as conn:
            conn.execute("DELETE FROM jira_duplicate_case_fts")
            conn.execute("DELETE FROM jira_duplicate_case_index")
            for case in cases:
                conn.execute(
                    """
                    INSERT INTO jira_duplicate_case_index (
                        issue_key, summary, description, domain, module, status, solution,
                        versions, fix_versions, updated, search_text
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        case["issue_key"],
                        case["summary"],
                        case["description"],
                        case["domain"],
                        case["module"],
                        case["status"],
                        case["solution"],
                        case["versions"],
                        case["fix_versions"],
                        case["updated"],
                        case["search_text"],
                    ),
                )
                conn.execute(
                    "INSERT INTO jira_duplicate_case_fts (issue_key, search_text) VALUES (?, ?)",
                    (case["issue_key"], case["search_text"]),
                )
            conn.execute(
                """
                INSERT INTO jira_duplicate_index_state (key, value, updated_at)
                VALUES ('embedding_backend', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (embedding_backend, now),
            )
            conn.execute(
                """
                INSERT INTO jira_duplicate_index_state (key, value, updated_at)
                VALUES ('case_count', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (str(len(cases)), now),
            )
            conn.execute(
                """
                INSERT INTO jira_duplicate_index_state (key, value, updated_at)
                VALUES ('normalizer_version', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (normalizer_version, now),
            )
            conn.execute(
                """
                INSERT INTO jira_duplicate_index_state (key, value, updated_at)
                VALUES ('source_db_path', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (source_db_path, now),
            )

    def case_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM jira_duplicate_case_index").fetchone()
        return int(row["count"] if row is not None else 0)

    def indexed_embedding_backend(self) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM jira_duplicate_index_state WHERE key = 'embedding_backend'"
            ).fetchone()
        return str(row["value"] or "") if row is not None else ""

    def indexed_normalizer_version(self) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM jira_duplicate_index_state WHERE key = 'normalizer_version'"
            ).fetchone()
        return str(row["value"] or "") if row is not None else ""

    def indexed_source_db_path(self) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM jira_duplicate_index_state WHERE key = 'source_db_path'"
            ).fetchone()
        return str(row["value"] or "") if row is not None else ""

    def list_index_cases(self) -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM jira_duplicate_case_index").fetchall()
        return [dict(row) for row in rows]

    def fts_search(self, query: str, *, limit: int) -> list[tuple[float, dict[str, str]]]:
        normalized = " ".join(query.strip().split())
        if normalized == "":
            return []
        terms = self._fts_terms(normalized)
        if not terms:
            return []
        quoted_terms = []
        for term in terms:
            escaped = term.replace('"', '""')
            quoted_terms.append(f'"{escaped}"')
        fts_query = " OR ".join(quoted_terms)
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT c.*, bm25(f) AS rank_score
                    FROM jira_duplicate_case_fts f
                    JOIN jira_duplicate_case_index c ON c.issue_key = f.issue_key
                    WHERE jira_duplicate_case_fts MATCH ?
                    ORDER BY rank_score ASC
                    LIMIT ?
                    """,
                    (fts_query, limit),
                ).fetchall()
        except sqlite3.OperationalError:
            return []
        results: list[tuple[float, dict[str, str]]] = []
        for row in rows:
            score = 1.0 / (1.0 + abs(float(row["rank_score"] or 0.0)))
            results.append((score, dict(row)))
        return results

    def get_index_cases(self, issue_keys: list[str]) -> dict[str, dict[str, str]]:
        if not issue_keys:
            return {}
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM jira_duplicate_case_index WHERE issue_key IN ({','.join('?' for _ in issue_keys)})",
                issue_keys,
            ).fetchall()
        return {str(row["issue_key"]): dict(row) for row in rows}

    def _fts_terms(self, text: str) -> list[str]:
        import re

        candidates = re.findall(r"[A-Za-z0-9_+.#-]{2,}|[\u4e00-\u9fff]{2,}", text.lower())
        compact = re.sub(r"\s+", "", text)
        if 3 <= len(compact) <= 80:
            candidates.insert(0, compact)
        seen: set[str] = set()
        terms: list[str] = []
        for candidate in candidates:
            value = candidate.strip()
            if len(value) < 2 or value in seen:
                continue
            seen.add(value)
            terms.append(value[:80])
            if len(terms) >= 8:
                break
        return terms

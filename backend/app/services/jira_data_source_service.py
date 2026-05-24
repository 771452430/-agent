"""Jira 支持问题历史数据同步服务。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from ..schemas import (
    JiraDataSourceRuntimeSettings,
    JiraDataSourceSettings,
    JiraDataSourceTestResponse,
    JiraDataSyncRun,
    UpdateJiraDataSourceSettingsRequest,
)
from ..settings import AppSettings
from .jira_data_source_store import JiraDataSourceStore


TOKEN_URL = "https://c1.yonyoucloud.com/iuap-api-auth/open-auth/selfAppAuth/getAccessToken"
JIRA_SUPPORT_URL = "https://c1.yonyoucloud.com/iuap-api-gateway/qyic8c7o/current_yonbip_default_sys/pm/jira/support"
DEFAULT_JIRA_APP_KEY = "97a980c631e74a0bbaf67da26993958d"
CONFIG_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "jira_config_data.json"
LEGACY_CONFIG_KEY = b"jira_skill_shared_key_v2024_secure"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JiraDataSourceService:
    """把原 jira-data-query 的数据拉取能力接入后端设置中心。"""

    def __init__(self, store: JiraDataSourceStore, settings: AppSettings) -> None:
        self.store = store
        self.settings = settings
        self._init_data = self._load_init_data()
        self._legacy_config_cache: dict[str, str] | None = None

    def _load_init_data(self) -> dict[str, list[dict[str, Any]]]:
        if not CONFIG_DATA_PATH.exists():
            return {"jira_projects": [], "jira_domain_modules": []}
        with CONFIG_DATA_PATH.open("r", encoding="utf-8") as file:
            raw = json.load(file)
        return {
            "jira_projects": list(raw.get("jira_projects") or []),
            "jira_domain_modules": list(raw.get("jira_domain_modules") or []),
        }

    def _mask_value(self, raw_value: str | None) -> str | None:
        normalized = (raw_value or "").strip()
        if normalized == "":
            return None
        if len(normalized) <= 10:
            return "*" * len(normalized)
        return normalized[:6] + ("*" * max(len(normalized) - 10, 1)) + normalized[-4:]

    def _decrypt_legacy_config(self) -> dict[str, str]:
        if self._legacy_config_cache is not None:
            return self._legacy_config_cache
        self._legacy_config_cache = {}
        legacy_dir = self.settings.jira_legacy_skill_dir
        if legacy_dir is None:
            return self._legacy_config_cache
        config_file = legacy_dir.expanduser() / "jira_config.enc"
        if not config_file.exists():
            return self._legacy_config_cache
        try:
            encrypted = base64.b64decode(config_file.read_bytes())
            decoded = bytearray()
            for index, byte in enumerate(encrypted):
                decoded.append(byte ^ LEGACY_CONFIG_KEY[index % len(LEGACY_CONFIG_KEY)])
            payload = json.loads(bytes(decoded).decode("utf-8"))
            if isinstance(payload, dict):
                self._legacy_config_cache = {
                    str(key): str(value)
                    for key, value in payload.items()
                    if str(value).strip() != ""
                }
        except Exception:
            self._legacy_config_cache = {}
        return self._legacy_config_cache

    def _resolve_runtime_credentials(
        self,
        runtime: JiraDataSourceRuntimeSettings,
    ) -> tuple[JiraDataSourceRuntimeSettings, str]:
        app_key = (runtime.app_key or "").strip()
        app_secret = (runtime.app_secret or "").strip()
        if app_secret != "":
            return runtime, "settings"

        env_key = (self.settings.jira_app_key or "").strip()
        env_secret = (self.settings.jira_app_secret or "").strip()
        if env_secret != "":
            return (
                runtime.model_copy(
                    update={
                        "app_key": app_key or env_key or DEFAULT_JIRA_APP_KEY,
                        "app_secret": env_secret,
                    }
                ),
                "environment",
            )

        legacy_config = self._decrypt_legacy_config()
        legacy_key = (legacy_config.get("JIRA_APP_KEY") or "").strip()
        legacy_secret = (legacy_config.get("JIRA_APP_SECRET") or "").strip()
        if legacy_secret != "":
            return (
                runtime.model_copy(
                    update={
                        "app_key": app_key or legacy_key or DEFAULT_JIRA_APP_KEY,
                        "app_secret": legacy_secret,
                    }
                ),
                "legacy_skill",
            )

        return runtime.model_copy(update={"app_key": app_key or env_key or DEFAULT_JIRA_APP_KEY}), "none"

    def _default_runtime(self) -> JiraDataSourceRuntimeSettings:
        now = _utc_now()
        return JiraDataSourceRuntimeSettings(
            enabled=False,
            db_path=str(self.settings.jira_support_db_path),
            app_key=self.settings.jira_app_key,
            app_secret=None,
            sync_keyword="工作台",
            sync_date_range="本年",
            sync_interval_minutes=1440,
            created_at=now,
            updated_at=now,
        )

    def _runtime_to_public(self, runtime: JiraDataSourceRuntimeSettings) -> JiraDataSourceSettings:
        latest = self.store.latest_run()
        effective_runtime, credential_source = self._resolve_runtime_credentials(runtime)
        app_secret = (effective_runtime.app_secret or "").strip()
        return JiraDataSourceSettings(
            enabled=runtime.enabled,
            db_path=runtime.db_path,
            app_key=effective_runtime.app_key or "",
            has_app_secret=app_secret != "",
            app_secret_masked=self._mask_value(app_secret),
            credential_source=credential_source,
            sync_keyword=runtime.sync_keyword,
            sync_date_range=runtime.sync_date_range,
            sync_interval_minutes=runtime.sync_interval_minutes,
            last_sync_status=latest.status if latest is not None else None,
            last_sync_at=latest.ended_at if latest is not None else None,
            last_error_message=latest.error_message if latest is not None else None,
        )

    def get_runtime_settings(self) -> JiraDataSourceRuntimeSettings:
        stored = self.store.get_runtime_settings()
        if stored is not None:
            return stored
        return self._default_runtime()

    def get_public_settings(self) -> JiraDataSourceSettings:
        return self._runtime_to_public(self.get_runtime_settings())

    def update_settings(self, request_data: UpdateJiraDataSourceSettingsRequest) -> JiraDataSourceSettings:
        current = self.get_runtime_settings()
        db_path = current.db_path
        app_key = current.app_key
        app_secret = current.app_secret
        sync_keyword = current.sync_keyword
        sync_date_range = current.sync_date_range
        sync_interval_minutes = current.sync_interval_minutes
        enabled = current.enabled
        if request_data.enabled is not None:
            enabled = request_data.enabled
        if request_data.db_path is not None:
            db_path = request_data.db_path.strip() or str(self.settings.jira_support_db_path)
        if request_data.app_key is not None:
            app_key = request_data.app_key.strip() or None
        if request_data.app_secret is not None:
            app_secret = request_data.app_secret.strip() or None
        if request_data.sync_keyword is not None:
            sync_keyword = request_data.sync_keyword.strip() or "工作台"
        if request_data.sync_date_range is not None:
            sync_date_range = request_data.sync_date_range.strip() or "本年"
        if request_data.sync_interval_minutes is not None:
            sync_interval_minutes = request_data.sync_interval_minutes
        saved = self.store.save_runtime_settings(
            JiraDataSourceRuntimeSettings(
                enabled=enabled,
                db_path=db_path,
                app_key=app_key,
                app_secret=app_secret,
                sync_keyword=sync_keyword,
                sync_date_range=sync_date_range,
                sync_interval_minutes=sync_interval_minutes,
                created_at=current.created_at,
                updated_at=_utc_now(),
            )
        )
        return self._runtime_to_public(saved)

    def list_runs(self) -> list[JiraDataSyncRun]:
        return self.store.list_runs()

    def test_settings(self) -> JiraDataSourceTestResponse:
        runtime, credential_source = self._resolve_runtime_credentials(self.get_runtime_settings())
        if (runtime.app_secret or "").strip() == "":
            return JiraDataSourceTestResponse(ok=False, message="缺少 JIRA_APP_SECRET，请先保存密钥。")
        matches = self.smart_search(runtime.sync_keyword)
        if not matches:
            return JiraDataSourceTestResponse(ok=False, message="未匹配到项目或模块，请调整同步关键字。")
        preview = [f"{item['project_name']} - {item['domain_module']}" for item in matches[:8]]
        return JiraDataSourceTestResponse(
            ok=True,
            message=f"已匹配到 {len(matches)} 个项目/模块，可执行同步。密钥来源：{credential_source}。",
            matched_count=len(matches),
            matched_preview=preview,
        )

    def sync_now(self, reindex_callback: Any | None = None) -> JiraDataSyncRun:
        runtime, _credential_source = self._resolve_runtime_credentials(self.get_runtime_settings())
        if (runtime.app_secret or "").strip() == "":
            raise HTTPException(status_code=400, detail="缺少 JIRA_APP_SECRET，请先在 Jira 数据源设置中保存。")
        matches = self.smart_search(runtime.sync_keyword)
        if not matches:
            raise HTTPException(status_code=400, detail=f"未匹配到同步关键字：{runtime.sync_keyword}")

        db_path = str(Path(runtime.db_path).expanduser())
        run = self.store.create_run(keyword=runtime.sync_keyword, date_range=runtime.sync_date_range, db_path=db_path)
        try:
            self._ensure_db(db_path)
            created_start, created_end = self.parse_date_range(runtime.sync_date_range)
            total_fetched = 0
            total_inserted = 0
            total_deleted = 0
            for match in matches:
                records = self.query_jira_data_all(
                    app_key=runtime.app_key or DEFAULT_JIRA_APP_KEY,
                    app_secret=runtime.app_secret or "",
                    project_id=match["project_id"],
                    domain_module=match["domain_module"],
                    created_start=created_start,
                    created_end=created_end,
                )
                total_fetched += len(records)
                if records:
                    inserted, deleted = self.save_to_database(
                        db_path,
                        records,
                        query_project_id=match["project_id"],
                        query_domain_module=match["domain_module"],
                    )
                    total_inserted += inserted
                    total_deleted += deleted
            reindexed_count = 0
            if reindex_callback is not None:
                result = reindex_callback(db_path)
                reindexed_count = int(result.get("indexed_count") or 0)
            summary = (
                f"同步完成：匹配 {len(matches)} 个项目/模块，拉取 {total_fetched} 条，"
                f"写入 {total_inserted} 条，重建索引 {reindexed_count} 条。"
            )
            return self.store.finish_run(
                run.id,
                status="success",
                matched_count=len(matches),
                fetched_count=total_fetched,
                inserted_count=total_inserted,
                deleted_count=total_deleted,
                reindexed_count=reindexed_count,
                summary=summary,
            )
        except Exception as exc:
            if isinstance(exc, HTTPException):
                message = str(exc.detail)
            else:
                message = str(exc)
            return self.store.finish_run(
                run.id,
                status="failed",
                matched_count=len(matches),
                fetched_count=0,
                inserted_count=0,
                deleted_count=0,
                reindexed_count=0,
                summary="同步失败",
                error_message=message,
            )

    def due_for_sync(self) -> bool:
        runtime = self.get_runtime_settings()
        if not runtime.enabled:
            return False
        latest = self.store.latest_run()
        if latest is None or latest.ended_at is None:
            return True
        next_run_at = latest.ended_at + timedelta(minutes=runtime.sync_interval_minutes)
        return _utc_now() >= next_run_at

    def smart_search(self, keyword: str) -> list[dict[str, str]]:
        normalized = (keyword or "").strip().lower()
        if normalized == "":
            return []
        modules = self._init_data.get("jira_domain_modules", [])
        matches: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for module in modules:
            haystack = " ".join(
                str(module.get(key) or "")
                for key in ("project_id", "pkey", "project_name", "domain_name", "domain_module")
            ).lower()
            if normalized not in haystack:
                continue
            project_id = str(module.get("project_id") or "")
            domain_module = str(module.get("domain_module") or "")
            key = (project_id, domain_module)
            if project_id == "" or domain_module == "" or key in seen:
                continue
            seen.add(key)
            matches.append(
                {
                    "project_id": project_id,
                    "project_name": str(module.get("project_name") or ""),
                    "domain_module": domain_module,
                }
            )
        return matches

    def parse_date_range(self, date_range_desc: str) -> tuple[str, str]:
        now = datetime.now()
        created_end = now.strftime("%Y-%m-%d %H:%M")
        value = (date_range_desc or "").strip()
        if value == "":
            created_start = f"{now.year}-01-01 00:00"
        elif "最近三天" in value or "近三天" in value:
            created_start = (now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M")
        elif "本周" in value:
            created_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d 00:00")
        elif "上周" in value:
            start = now - timedelta(days=now.weekday() + 7)
            end = now - timedelta(days=now.weekday())
            created_start = start.strftime("%Y-%m-%d 00:00")
            created_end = end.strftime("%Y-%m-%d 23:59")
        elif "本月" in value:
            created_start = f"{now.year}-{now.month:02d}-01 00:00"
        elif "本季度" in value:
            month = ((now.month - 1) // 3) * 3 + 1
            created_start = f"{now.year}-{month:02d}-01 00:00"
        elif "1季度" in value or "第一季度" in value:
            created_start = f"{now.year}-01-01 00:00"
            created_end = f"{now.year}-03-31 23:59"
        elif "2季度" in value or "第二季度" in value:
            created_start = f"{now.year}-04-01 00:00"
            created_end = f"{now.year}-06-30 23:59"
        elif "3季度" in value or "第三季度" in value:
            created_start = f"{now.year}-07-01 00:00"
            created_end = f"{now.year}-09-30 23:59"
        elif "4季度" in value or "第四季度" in value:
            created_start = f"{now.year}-10-01 00:00"
            created_end = f"{now.year}-12-31 23:59"
        elif "本年" in value or "今年" in value or "当年" in value:
            created_start = f"{now.year}-01-01 00:00"
        elif "去年" in value or "上一年" in value:
            year = now.year - 1
            created_start = f"{year}-01-01 00:00"
            created_end = f"{year}-12-31 23:59"
        elif "前年" in value:
            year = now.year - 2
            created_start = f"{year}-01-01 00:00"
            created_end = f"{year}-12-31 23:59"
        elif value.endswith("年") and value[:-1].isdigit() and len(value[:-1]) == 4:
            year = int(value[:-1])
            created_start = f"{year}-01-01 00:00"
            created_end = f"{year}-12-31 23:59"
        elif value.isdigit() and len(value) == 4:
            year = int(value)
            created_start = f"{year}-01-01 00:00"
            created_end = f"{year}-12-31 23:59"
        else:
            created_start = f"{now.year}-01-01 00:00"
        return created_start, created_end

    def generate_signature(self, app_key: str, timestamp: int, app_secret: str) -> str:
        payload = f"appKey{app_key}timestamp{timestamp}"
        digest = hmac.new(app_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")

    def get_access_token(self, *, app_key: str, app_secret: str) -> str:
        timestamp = int(time.time() * 1000)
        signature = self.generate_signature(app_key, timestamp, app_secret)
        query = urllib.parse.urlencode({"appKey": app_key, "timestamp": timestamp, "signature": signature})
        payload = self._request_json("GET", TOKEN_URL + "?" + query)
        if payload.get("code") == "00000":
            token = str((payload.get("data") or {}).get("access_token") or "")
            if token != "":
                return token
        raise RuntimeError("获取 Jira access_token 失败。")

    def query_jira_data_all(
        self,
        *,
        app_key: str,
        app_secret: str,
        project_id: str,
        domain_module: str,
        created_start: str,
        created_end: str,
    ) -> list[dict[str, Any]]:
        access_token = self.get_access_token(app_key=app_key, app_secret=app_secret)
        all_records: list[dict[str, Any]] = []
        page_index = 1
        page_size = 100
        while True:
            body = {
                "pageSize": str(page_size),
                "pageIndex": str(page_index),
                "createdStart": created_start,
                "createdEnd": created_end,
                "project": project_id,
                "domainModule": domain_module,
            }
            url = f"{JIRA_SUPPORT_URL}?access_token={urllib.parse.quote(access_token, safe='')}"
            payload = self._request_json("POST", url, body)
            if payload.get("code") != "200":
                raise RuntimeError(f"查询 Jira 数据失败：{payload.get('message') or payload}")
            records = list(((payload.get("data") or {}).get("recordList") or []))
            if not records:
                break
            all_records.extend(records)
            if len(records) < page_size:
                break
            page_index += 1
            if page_index > 100:
                break
        return all_records

    def _request_json(self, method: str, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read().decode("utf-8")
        payload = json.loads(raw or "{}")
        return payload if isinstance(payload, dict) else {}

    def _ensure_db(self, db_path: str) -> None:
        path = Path(db_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jira_projects (
                    project_id INTEGER PRIMARY KEY,
                    project_key TEXT,
                    project_name TEXT NOT NULL,
                    project_full_name TEXT,
                    agent_code TEXT,
                    agent_name TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jira_domain_modules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT,
                    pkey TEXT,
                    project_name TEXT,
                    domain_id TEXT,
                    domain_name TEXT,
                    module_id TEXT,
                    domain_module TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jira_support_issues (
                    id TEXT PRIMARY KEY,
                    issue_key TEXT UNIQUE,
                    summary TEXT,
                    feature_type TEXT,
                    status TEXT,
                    priority TEXT,
                    domain TEXT,
                    module TEXT,
                    dev_type TEXT,
                    return_type TEXT,
                    sop_vdm TEXT,
                    created TEXT,
                    updated TEXT,
                    confirm_time TEXT,
                    solution TEXT,
                    description TEXT,
                    project_key TEXT,
                    project_name TEXT,
                    assignee_key TEXT,
                    assignee_name TEXT,
                    assignee_display_name TEXT,
                    assignee_email TEXT,
                    reporter_key TEXT,
                    reporter_name TEXT,
                    reporter_display_name TEXT,
                    reporter_email TEXT,
                    versions TEXT,
                    fix_versions TEXT,
                    query_project_id INTEGER,
                    query_domain_module TEXT,
                    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self._seed_reference_data(conn)

    def _seed_reference_data(self, conn: sqlite3.Connection) -> None:
        projects = self._init_data.get("jira_projects", [])
        modules = self._init_data.get("jira_domain_modules", [])
        project_count = conn.execute("SELECT COUNT(*) FROM jira_projects").fetchone()[0]
        if project_count == 0 and projects:
            conn.executemany(
                """
                INSERT INTO jira_projects (
                    project_id, project_key, project_name, project_full_name, agent_code, agent_name
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item.get("project_id"),
                        item.get("project_key"),
                        item.get("project_name"),
                        item.get("project_full_name"),
                        item.get("agent_code"),
                        item.get("agent_name"),
                    )
                    for item in projects
                ],
            )
        module_count = conn.execute("SELECT COUNT(*) FROM jira_domain_modules").fetchone()[0]
        if module_count == 0 and modules:
            conn.executemany(
                """
                INSERT INTO jira_domain_modules (
                    project_id, pkey, project_name, domain_id, domain_name, module_id, domain_module
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item.get("project_id"),
                        item.get("pkey"),
                        item.get("project_name"),
                        item.get("domain_id"),
                        item.get("domain_name"),
                        item.get("module_id"),
                        item.get("domain_module"),
                    )
                    for item in modules
                ],
            )

    def save_to_database(
        self,
        db_path: str,
        records: list[dict[str, Any]],
        *,
        query_project_id: str,
        query_domain_module: str,
    ) -> tuple[int, int]:
        inserted_count = 0
        deleted_count = 0
        with sqlite3.connect(Path(db_path).expanduser()) as conn:
            for record in records:
                flat = self.flatten_record(record, query_project_id, query_domain_module)
                deleted_count += conn.execute(
                    "DELETE FROM jira_support_issues WHERE id = ? OR issue_key = ?",
                    (flat["id"], flat["issue_key"]),
                ).rowcount
                conn.execute(
                    """
                    INSERT INTO jira_support_issues (
                        id, issue_key, summary, feature_type, status, priority,
                        domain, module, dev_type, return_type, sop_vdm,
                        created, updated, confirm_time, solution, description,
                        project_key, project_name,
                        assignee_key, assignee_name, assignee_display_name, assignee_email,
                        reporter_key, reporter_name, reporter_display_name, reporter_email,
                        versions, fix_versions, query_project_id, query_domain_module
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(flat[key] for key in (
                        "id",
                        "issue_key",
                        "summary",
                        "feature_type",
                        "status",
                        "priority",
                        "domain",
                        "module",
                        "dev_type",
                        "return_type",
                        "sop_vdm",
                        "created",
                        "updated",
                        "confirm_time",
                        "solution",
                        "description",
                        "project_key",
                        "project_name",
                        "assignee_key",
                        "assignee_name",
                        "assignee_display_name",
                        "assignee_email",
                        "reporter_key",
                        "reporter_name",
                        "reporter_display_name",
                        "reporter_email",
                        "versions",
                        "fix_versions",
                        "query_project_id",
                        "query_domain_module",
                    )),
                )
                inserted_count += 1
        return inserted_count, deleted_count

    def flatten_record(self, record: dict[str, Any], query_project_id: str, query_domain_module: str) -> dict[str, Any]:
        project = record.get("project") if isinstance(record.get("project"), dict) else {}
        assignee = record.get("assignee") if isinstance(record.get("assignee"), dict) else {}
        reporter = record.get("reporter") if isinstance(record.get("reporter"), dict) else {}
        versions = record.get("versions") or []
        fix_versions = record.get("fixVersions") or []
        return {
            "id": record.get("id"),
            "issue_key": record.get("issueKey"),
            "summary": record.get("summary"),
            "feature_type": record.get("featureType"),
            "status": record.get("status"),
            "priority": record.get("priority"),
            "domain": record.get("domain"),
            "module": record.get("module"),
            "dev_type": record.get("devType"),
            "return_type": record.get("returnType"),
            "sop_vdm": record.get("sopVdm"),
            "created": record.get("created"),
            "updated": record.get("updated"),
            "confirm_time": record.get("confirmTime"),
            "solution": record.get("solution"),
            "description": record.get("description"),
            "project_key": project.get("projectKey"),
            "project_name": project.get("projectName"),
            "assignee_key": assignee.get("key"),
            "assignee_name": assignee.get("name"),
            "assignee_display_name": assignee.get("displayName"),
            "assignee_email": assignee.get("emailAddress"),
            "reporter_key": reporter.get("key"),
            "reporter_name": reporter.get("name"),
            "reporter_display_name": reporter.get("displayName"),
            "reporter_email": reporter.get("emailAddress"),
            "versions": json.dumps(versions, ensure_ascii=False) if versions else None,
            "fix_versions": json.dumps(fix_versions, ensure_ascii=False) if fix_versions else None,
            "query_project_id": query_project_id,
            "query_domain_module": query_domain_module,
        }

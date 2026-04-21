"""支持问题 Agent 的持久化存储。

本模块在原有“Agent 配置 + 运行记录”之外，新增了三类反哺数据：
- 反馈事实快照：保存飞书表里的当前最新人工反馈；
- 反馈历史：保存关键反馈字段的变化轨迹，便于后续回溯；
- 案例候选池 / digest：支撑案例审核与周期汇总。

设计原则：
- 飞书继续承担协作入口，不改变业务同学的使用习惯；
- SQLite 负责结构化沉淀，方便查询、筛选、统计和邮件汇总；
- 正式案例进入知识库，不写入源码目录。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from ..schemas import (
    ModelConfig,
    SupportIssueAgentConfig,
    SupportIssueCaseCandidate,
    SupportIssueCategoryStat,
    SupportIssueDigestRun,
    SupportIssueFeedbackFact,
    SupportIssueGraphTraceEvent,
    SupportIssueNotificationEvent,
    SupportIssueOwnerRule,
    SupportIssueRowResult,
    SupportIssueRun,
)


DIGEST_TIMEZONE = ZoneInfo("Asia/Shanghai")
DIGEST_WEEKDAY = 0
DIGEST_HOUR = 9
DIGEST_MINUTE = 0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_load_json_list(raw: str | None) -> list[object]:
    """把 SQLite 中的 JSON 文本安全地还原成 list。

    这里统一做一层兜底，避免单个旧数据或空值把整个页面打崩。
    """

    try:
        payload = json.loads(raw or "[]")
    except Exception:
        return []
    return payload if isinstance(payload, list) else []


def _safe_load_json_dict(raw: str | None) -> dict[str, object]:
    """把 SQLite 中的 JSON 文本安全地还原成 dict。"""

    try:
        payload = json.loads(raw or "{}")
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_email_list(emails: list[str] | None) -> list[str]:
    """统一规范 digest 收件人列表。

    规则非常保守：
    - 去除空白；
    - 保持用户输入顺序；
    - 做简单去重。
    """

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in emails or []:
        item = str(raw).strip()
        if item == "" or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def _normalize_owner_rules(rules: list[SupportIssueOwnerRule] | None) -> list[SupportIssueOwnerRule]:
    """统一清洗“模块 -> 负责人”规则。"""

    normalized: list[SupportIssueOwnerRule] = []
    for item in rules or []:
        rule = item if isinstance(item, SupportIssueOwnerRule) else SupportIssueOwnerRule.model_validate(item)
        module_value = rule.module_value.strip()
        yht_user_id = rule.yht_user_id.strip()
        if module_value == "" or yht_user_id == "":
            continue
        normalized.append(SupportIssueOwnerRule(module_value=module_value, yht_user_id=yht_user_id))
    return normalized


def _next_digest_at(base_time: datetime | None = None) -> datetime:
    """计算下一个每周一 09:00（Asia/Shanghai）的 UTC 时间。

    这样做的原因：
    - 调度器本身跑在 UTC 时区更稳定；
    - 业务表达仍然固定为“上海时区周一 09:00”。
    """

    current_utc = base_time or _utc_now()
    current_local = current_utc.astimezone(DIGEST_TIMEZONE)
    current_target = current_local.replace(hour=DIGEST_HOUR, minute=DIGEST_MINUTE, second=0, microsecond=0)

    days_ahead = (DIGEST_WEEKDAY - current_local.weekday()) % 7
    if days_ahead == 0 and current_local >= current_target:
        days_ahead = 7

    next_local = current_target + timedelta(days=days_ahead)
    return next_local.astimezone(timezone.utc)


def _next_digest_after_completed_run(base_time: datetime) -> datetime:
    """计算 digest 实际执行完成后的下一次计划时间。

    与 `_next_digest_at()` 的区别：
    - `_next_digest_at()` 适用于“首次建档 / 开启调度”，周一 09:00 之前允许排到当天；
    - 这个函数适用于“某次 digest 已经真的跑完了”，即使是周一 09:00 之前手动执行，
      也应该直接排到下周，避免当天再次自动跑一遍。
    """

    current_local = base_time.astimezone(DIGEST_TIMEZONE)
    current_target = current_local.replace(hour=DIGEST_HOUR, minute=DIGEST_MINUTE, second=0, microsecond=0)
    if current_local.weekday() == DIGEST_WEEKDAY and current_local < current_target:
        return (current_target + timedelta(days=7)).astimezone(timezone.utc)
    return _next_digest_at(base_time)


class SupportIssueStore:
    """负责支持问题 Agent 的配置、运行记录与反哺数据持久化。"""

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
        """初始化支持问题模块的全部持久化表。

        这里采用“建表 + 按列迁移”的轻量策略：
        - 新环境可以一次性建出完整结构；
        - 老环境会按需补齐新增列；
        - 避免引入独立 migration 框架，保持 demo 工程的可维护性。
        """

        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS support_issue_agents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    poll_interval_minutes INTEGER NOT NULL,
                    feishu_bitable_url TEXT,
                    feishu_app_token TEXT NOT NULL,
                    feishu_table_id TEXT NOT NULL,
                    model_config_json TEXT NOT NULL,
                    knowledge_scope_type TEXT NOT NULL,
                    knowledge_scope_id TEXT,
                    question_field_name TEXT NOT NULL,
                    answer_field_name TEXT NOT NULL,
                    link_field_name TEXT NOT NULL,
                    progress_field_name TEXT NOT NULL DEFAULT '回复进度',
                    status_field_name TEXT NOT NULL,
                    module_field_name TEXT NOT NULL DEFAULT '负责模块',
                    registrant_field_name TEXT NOT NULL DEFAULT '登记人',
                    feedback_result_field_name TEXT NOT NULL DEFAULT '人工处理结果',
                    feedback_final_answer_field_name TEXT NOT NULL DEFAULT '人工最终方案',
                    feedback_comment_field_name TEXT NOT NULL DEFAULT '反馈备注',
                    confidence_field_name TEXT NOT NULL DEFAULT 'AI置信度',
                    hit_count_field_name TEXT NOT NULL DEFAULT '命中知识数',
                    support_owner_rules_json TEXT NOT NULL DEFAULT '[]',
                    fallback_support_yht_user_id TEXT NOT NULL DEFAULT '',
                    digest_enabled INTEGER NOT NULL DEFAULT 0,
                    digest_recipient_emails_json TEXT NOT NULL DEFAULT '[]',
                    case_review_enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_run_at TEXT,
                    next_run_at TEXT,
                    last_digest_at TEXT,
                    next_digest_at TEXT
                );

                CREATE TABLE IF NOT EXISTS support_issue_runs (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    fetched_row_count INTEGER NOT NULL,
                    processed_row_count INTEGER NOT NULL,
                    generated_count INTEGER NOT NULL,
                    manual_review_count INTEGER NOT NULL DEFAULT 0,
                    no_hit_count INTEGER NOT NULL,
                    failed_count INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    error_message TEXT,
                    row_results_json TEXT NOT NULL,
                    graph_trace_json TEXT NOT NULL DEFAULT '[]'
                );

                CREATE TABLE IF NOT EXISTS support_issue_feedback_facts (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    question TEXT NOT NULL DEFAULT '',
                    progress_value TEXT NOT NULL DEFAULT '',
                    ai_solution TEXT NOT NULL DEFAULT '',
                    related_links_json TEXT NOT NULL DEFAULT '[]',
                    feedback_result TEXT NOT NULL DEFAULT '',
                    feedback_final_answer TEXT NOT NULL DEFAULT '',
                    feedback_comment TEXT NOT NULL DEFAULT '',
                    confidence_score REAL NOT NULL DEFAULT 0,
                    retrieval_hit_count INTEGER NOT NULL DEFAULT 0,
                    question_category TEXT NOT NULL DEFAULT '',
                    source_bitable_url TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_synced_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS support_issue_feedback_history (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    changed_fields_json TEXT NOT NULL DEFAULT '[]',
                    previous_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    current_snapshot_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS support_issue_case_candidates (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    question TEXT NOT NULL DEFAULT '',
                    ai_draft TEXT NOT NULL DEFAULT '',
                    feedback_result TEXT NOT NULL DEFAULT '',
                    final_solution TEXT NOT NULL DEFAULT '',
                    feedback_comment TEXT NOT NULL DEFAULT '',
                    confidence_score REAL NOT NULL DEFAULT 0,
                    retrieval_hit_count INTEGER NOT NULL DEFAULT 0,
                    question_category TEXT NOT NULL DEFAULT '',
                    related_links_json TEXT NOT NULL DEFAULT '[]',
                    source_bitable_url TEXT NOT NULL DEFAULT '',
                    review_comment TEXT NOT NULL DEFAULT '',
                    knowledge_document_id TEXT,
                    approved_at TEXT,
                    approved_by TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS support_issue_digest_runs (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    trigger_source TEXT NOT NULL DEFAULT 'manual',
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    recipient_emails_json TEXT NOT NULL DEFAULT '[]',
                    email_sent INTEGER NOT NULL DEFAULT 0,
                    email_subject TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    error_message TEXT,
                    total_processed_count INTEGER NOT NULL DEFAULT 0,
                    generated_count INTEGER NOT NULL DEFAULT 0,
                    manual_review_count INTEGER NOT NULL DEFAULT 0,
                    no_hit_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    acceptance_count INTEGER NOT NULL DEFAULT 0,
                    revised_acceptance_count INTEGER NOT NULL DEFAULT 0,
                    rejected_count INTEGER NOT NULL DEFAULT 0,
                    acceptance_rate REAL NOT NULL DEFAULT 0,
                    rejection_rate REAL NOT NULL DEFAULT 0,
                    low_confidence_rate REAL NOT NULL DEFAULT 0,
                    no_hit_rate REAL NOT NULL DEFAULT 0,
                    manual_rewrite_rate REAL NOT NULL DEFAULT 0,
                    top_categories_json TEXT NOT NULL DEFAULT '[]',
                    top_no_hit_topics_json TEXT NOT NULL DEFAULT '[]',
                    highlight_samples_json TEXT NOT NULL DEFAULT '[]',
                    knowledge_gap_suggestions_json TEXT NOT NULL DEFAULT '[]',
                    new_candidate_count INTEGER NOT NULL DEFAULT 0,
                    approved_candidate_count INTEGER NOT NULL DEFAULT 0,
                    graph_trace_json TEXT NOT NULL DEFAULT '[]'
                );

                CREATE TABLE IF NOT EXISTS support_issue_notification_events (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    recipient_user_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    error_message TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS support_issue_digest_items (
                    id TEXT PRIMARY KEY,
                    digest_run_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    record_id TEXT,
                    candidate_id TEXT,
                    item_type TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_support_issue_feedback_facts_agent_record
                    ON support_issue_feedback_facts(agent_id, record_id);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_support_issue_case_candidates_agent_record
                    ON support_issue_case_candidates(agent_id, record_id);

                CREATE INDEX IF NOT EXISTS idx_support_issue_digest_runs_agent_started
                    ON support_issue_digest_runs(agent_id, started_at DESC);

                CREATE INDEX IF NOT EXISTS idx_support_issue_feedback_history_agent_record
                    ON support_issue_feedback_history(agent_id, record_id, changed_at DESC);

                CREATE INDEX IF NOT EXISTS idx_support_issue_notification_events_agent_created
                    ON support_issue_notification_events(agent_id, created_at DESC);
                """
            )

            # 老数据迁移：在已有表上补齐新列。
            self._ensure_column(conn, "support_issue_agents", "feishu_bitable_url", "TEXT")
            self._ensure_column(conn, "support_issue_agents", "progress_field_name", "TEXT NOT NULL DEFAULT '回复进度'")
            self._ensure_column(conn, "support_issue_agents", "module_field_name", "TEXT NOT NULL DEFAULT '负责模块'")
            self._ensure_column(conn, "support_issue_agents", "registrant_field_name", "TEXT NOT NULL DEFAULT '登记人'")
            self._ensure_column(
                conn,
                "support_issue_agents",
                "feedback_result_field_name",
                "TEXT NOT NULL DEFAULT '人工处理结果'",
            )
            self._ensure_column(
                conn,
                "support_issue_agents",
                "feedback_final_answer_field_name",
                "TEXT NOT NULL DEFAULT '人工最终方案'",
            )
            self._ensure_column(
                conn,
                "support_issue_agents",
                "feedback_comment_field_name",
                "TEXT NOT NULL DEFAULT '反馈备注'",
            )
            self._ensure_column(
                conn,
                "support_issue_agents",
                "confidence_field_name",
                "TEXT NOT NULL DEFAULT 'AI置信度'",
            )
            self._ensure_column(
                conn,
                "support_issue_agents",
                "hit_count_field_name",
                "TEXT NOT NULL DEFAULT '命中知识数'",
            )
            self._ensure_column(
                conn,
                "support_issue_agents",
                "support_owner_rules_json",
                "TEXT NOT NULL DEFAULT '[]'",
            )
            self._ensure_column(
                conn,
                "support_issue_agents",
                "fallback_support_yht_user_id",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn,
                "support_issue_agents",
                "digest_enabled",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                conn,
                "support_issue_agents",
                "digest_recipient_emails_json",
                "TEXT NOT NULL DEFAULT '[]'",
            )
            self._ensure_column(
                conn,
                "support_issue_agents",
                "case_review_enabled",
                "INTEGER NOT NULL DEFAULT 1",
            )
            self._ensure_column(conn, "support_issue_agents", "last_digest_at", "TEXT")
            self._ensure_column(conn, "support_issue_agents", "next_digest_at", "TEXT")
            self._ensure_column(
                conn,
                "support_issue_runs",
                "manual_review_count",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                conn,
                "support_issue_runs",
                "graph_trace_json",
                "TEXT NOT NULL DEFAULT '[]'",
            )
            self._ensure_column(
                conn,
                "support_issue_digest_runs",
                "trigger_source",
                "TEXT NOT NULL DEFAULT 'manual'",
            )
            self._ensure_column(
                conn,
                "support_issue_digest_runs",
                "graph_trace_json",
                "TEXT NOT NULL DEFAULT '[]'",
            )

            # 把新增列的空值补齐为默认值，保证旧数据读取稳定。
            conn.execute(
                "UPDATE support_issue_agents SET progress_field_name = '回复进度' "
                "WHERE progress_field_name IS NULL OR progress_field_name = ''"
            )
            conn.execute(
                "UPDATE support_issue_agents SET module_field_name = '负责模块' "
                "WHERE module_field_name IS NULL OR module_field_name = ''"
            )
            conn.execute(
                "UPDATE support_issue_agents SET registrant_field_name = '登记人' "
                "WHERE registrant_field_name IS NULL OR registrant_field_name = ''"
            )
            conn.execute(
                "UPDATE support_issue_agents SET feedback_result_field_name = '人工处理结果' "
                "WHERE feedback_result_field_name IS NULL OR feedback_result_field_name = ''"
            )
            conn.execute(
                "UPDATE support_issue_agents SET feedback_final_answer_field_name = '人工最终方案' "
                "WHERE feedback_final_answer_field_name IS NULL OR feedback_final_answer_field_name = ''"
            )
            conn.execute(
                "UPDATE support_issue_agents SET feedback_comment_field_name = '反馈备注' "
                "WHERE feedback_comment_field_name IS NULL OR feedback_comment_field_name = ''"
            )
            conn.execute(
                "UPDATE support_issue_agents SET confidence_field_name = 'AI置信度' "
                "WHERE confidence_field_name IS NULL OR confidence_field_name = ''"
            )
            conn.execute(
                "UPDATE support_issue_agents SET hit_count_field_name = '命中知识数' "
                "WHERE hit_count_field_name IS NULL OR hit_count_field_name = ''"
            )
            conn.execute(
                "UPDATE support_issue_agents SET support_owner_rules_json = '[]' "
                "WHERE support_owner_rules_json IS NULL OR support_owner_rules_json = ''"
            )
            conn.execute(
                "UPDATE support_issue_agents SET fallback_support_yht_user_id = '' "
                "WHERE fallback_support_yht_user_id IS NULL"
            )
            conn.execute(
                "UPDATE support_issue_agents SET digest_recipient_emails_json = '[]' "
                "WHERE digest_recipient_emails_json IS NULL OR digest_recipient_emails_json = ''"
            )
            conn.execute(
                "UPDATE support_issue_agents SET case_review_enabled = 1 "
                "WHERE case_review_enabled IS NULL"
            )
            conn.execute(
                "UPDATE support_issue_runs SET graph_trace_json = '[]' "
                "WHERE graph_trace_json IS NULL OR graph_trace_json = ''"
            )
            conn.execute(
                "UPDATE support_issue_digest_runs SET graph_trace_json = '[]' "
                "WHERE graph_trace_json IS NULL OR graph_trace_json = ''"
            )

            # 案例候选池两态化迁移：
            # 历史版本里存在 `returned / rejected / analysis_only` 三种状态。
            # 现在统一折叠成 `pending_review`，界面上只保留“待审核 / 审核通过”。
            conn.execute(
                "UPDATE support_issue_case_candidates SET status = 'pending_review' "
                "WHERE status IS NULL OR status NOT IN ('pending_review', 'approved')"
            )

    def _fallback_bitable_url(self, app_token: str, table_id: str) -> str:
        normalized_app_token = app_token.strip()
        normalized_table_id = table_id.strip()
        if normalized_app_token == "" or normalized_table_id == "":
            return ""
        return f"https://feishu.cn/base/{normalized_app_token}?table={normalized_table_id}"

    def _parse_row_results(self, raw: str | None) -> list[SupportIssueRowResult]:
        items = _safe_load_json_list(raw)
        return [SupportIssueRowResult.model_validate(item) for item in items if isinstance(item, dict)]

    def _parse_graph_trace(self, raw: str | None) -> list[SupportIssueGraphTraceEvent]:
        """解析运行轨迹 JSON，并对旧数据做安全兜底。"""

        items = _safe_load_json_list(raw)
        return [SupportIssueGraphTraceEvent.model_validate(item) for item in items if isinstance(item, dict)]

    def _parse_category_stats(self, raw: str | None) -> list[SupportIssueCategoryStat]:
        items = _safe_load_json_list(raw)
        return [SupportIssueCategoryStat.model_validate(item) for item in items if isinstance(item, dict)]

    def _row_to_config(self, row: sqlite3.Row) -> SupportIssueAgentConfig:
        return SupportIssueAgentConfig(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            enabled=bool(row["enabled"]),
            poll_interval_minutes=int(row["poll_interval_minutes"]),
            feishu_bitable_url=row["feishu_bitable_url"]
            or self._fallback_bitable_url(row["feishu_app_token"] or "", row["feishu_table_id"] or ""),
            feishu_app_token=row["feishu_app_token"],
            feishu_table_id=row["feishu_table_id"],
            model_settings=ModelConfig.model_validate_json(row["model_config_json"]),
            knowledge_scope_type=row["knowledge_scope_type"],
            knowledge_scope_id=row["knowledge_scope_id"],
            question_field_name=row["question_field_name"],
            answer_field_name=row["answer_field_name"],
            link_field_name=row["link_field_name"],
            progress_field_name=row["progress_field_name"] or "回复进度",
            status_field_name=row["status_field_name"],
            module_field_name=row["module_field_name"] or "负责模块",
            registrant_field_name=row["registrant_field_name"] or "登记人",
            feedback_result_field_name=row["feedback_result_field_name"] or "人工处理结果",
            feedback_final_answer_field_name=row["feedback_final_answer_field_name"] or "人工最终方案",
            feedback_comment_field_name=row["feedback_comment_field_name"] or "反馈备注",
            confidence_field_name=row["confidence_field_name"] or "AI置信度",
            hit_count_field_name=row["hit_count_field_name"] or "命中知识数",
            support_owner_rules=_normalize_owner_rules(
                [item for item in _safe_load_json_list(row["support_owner_rules_json"]) if isinstance(item, dict)]
            ),
            fallback_support_yht_user_id=row["fallback_support_yht_user_id"] or "",
            digest_enabled=bool(row["digest_enabled"]),
            digest_recipient_emails=[
                str(item)
                for item in _safe_load_json_list(row["digest_recipient_emails_json"])
                if str(item).strip() != ""
            ],
            case_review_enabled=bool(row["case_review_enabled"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            last_run_at=datetime.fromisoformat(row["last_run_at"]) if row["last_run_at"] else None,
            next_run_at=datetime.fromisoformat(row["next_run_at"]) if row["next_run_at"] else None,
            last_digest_at=datetime.fromisoformat(row["last_digest_at"]) if row["last_digest_at"] else None,
            next_digest_at=datetime.fromisoformat(row["next_digest_at"]) if row["next_digest_at"] else None,
            last_run_status=row["last_run_status"],
            last_run_summary=row["last_run_summary"],
        )

    def _row_to_run(self, row: sqlite3.Row) -> SupportIssueRun:
        return SupportIssueRun(
            id=row["id"],
            agent_id=row["agent_id"],
            status=row["status"],
            started_at=datetime.fromisoformat(row["started_at"]),
            ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
            fetched_row_count=int(row["fetched_row_count"]),
            processed_row_count=int(row["processed_row_count"]),
            generated_count=int(row["generated_count"]),
            manual_review_count=int(row["manual_review_count"] or 0),
            no_hit_count=int(row["no_hit_count"]),
            failed_count=int(row["failed_count"]),
            summary=row["summary"] or "",
            error_message=row["error_message"],
            row_results=self._parse_row_results(row["row_results_json"]),
            graph_trace=self._parse_graph_trace(row["graph_trace_json"]),
        )

    def _row_to_feedback_fact(self, row: sqlite3.Row) -> SupportIssueFeedbackFact:
        return SupportIssueFeedbackFact(
            id=row["id"],
            agent_id=row["agent_id"],
            record_id=row["record_id"],
            question=row["question"] or "",
            progress_value=row["progress_value"] or "",
            ai_solution=row["ai_solution"] or "",
            related_links=[str(item) for item in _safe_load_json_list(row["related_links_json"]) if str(item).strip() != ""],
            feedback_result=row["feedback_result"] or "",
            feedback_final_answer=row["feedback_final_answer"] or "",
            feedback_comment=row["feedback_comment"] or "",
            confidence_score=float(row["confidence_score"] or 0.0),
            retrieval_hit_count=int(row["retrieval_hit_count"] or 0),
            question_category=row["question_category"] or "",
            source_bitable_url=row["source_bitable_url"] or "",
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            last_synced_at=datetime.fromisoformat(row["last_synced_at"]),
        )

    def _row_to_case_candidate(self, row: sqlite3.Row) -> SupportIssueCaseCandidate:
        normalized_status = row["status"] if row["status"] in {"pending_review", "approved"} else "pending_review"
        return SupportIssueCaseCandidate(
            id=row["id"],
            agent_id=row["agent_id"],
            record_id=row["record_id"],
            status=normalized_status,
            question=row["question"] or "",
            ai_draft=row["ai_draft"] or "",
            feedback_result=row["feedback_result"] or "",
            final_solution=row["final_solution"] or "",
            feedback_comment=row["feedback_comment"] or "",
            confidence_score=float(row["confidence_score"] or 0.0),
            retrieval_hit_count=int(row["retrieval_hit_count"] or 0),
            question_category=row["question_category"] or "",
            related_links=[str(item) for item in _safe_load_json_list(row["related_links_json"]) if str(item).strip() != ""],
            source_bitable_url=row["source_bitable_url"] or "",
            review_comment=row["review_comment"] or "",
            knowledge_document_id=row["knowledge_document_id"],
            approved_at=datetime.fromisoformat(row["approved_at"]) if row["approved_at"] else None,
            approved_by=row["approved_by"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _row_to_digest_run(self, row: sqlite3.Row) -> SupportIssueDigestRun:
        return SupportIssueDigestRun(
            id=row["id"],
            agent_id=row["agent_id"],
            status=row["status"],
            trigger_source=row["trigger_source"] or "manual",
            started_at=datetime.fromisoformat(row["started_at"]),
            ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
            period_start=datetime.fromisoformat(row["period_start"]),
            period_end=datetime.fromisoformat(row["period_end"]),
            recipient_emails=[
                str(item)
                for item in _safe_load_json_list(row["recipient_emails_json"])
                if str(item).strip() != ""
            ],
            email_sent=bool(row["email_sent"]),
            email_subject=row["email_subject"] or "",
            summary=row["summary"] or "",
            error_message=row["error_message"],
            total_processed_count=int(row["total_processed_count"] or 0),
            generated_count=int(row["generated_count"] or 0),
            manual_review_count=int(row["manual_review_count"] or 0),
            no_hit_count=int(row["no_hit_count"] or 0),
            failed_count=int(row["failed_count"] or 0),
            acceptance_count=int(row["acceptance_count"] or 0),
            revised_acceptance_count=int(row["revised_acceptance_count"] or 0),
            rejected_count=int(row["rejected_count"] or 0),
            acceptance_rate=float(row["acceptance_rate"] or 0.0),
            rejection_rate=float(row["rejection_rate"] or 0.0),
            low_confidence_rate=float(row["low_confidence_rate"] or 0.0),
            no_hit_rate=float(row["no_hit_rate"] or 0.0),
            manual_rewrite_rate=float(row["manual_rewrite_rate"] or 0.0),
            top_categories=self._parse_category_stats(row["top_categories_json"]),
            top_no_hit_topics=[
                str(item) for item in _safe_load_json_list(row["top_no_hit_topics_json"]) if str(item).strip() != ""
            ],
            highlight_samples=[
                str(item) for item in _safe_load_json_list(row["highlight_samples_json"]) if str(item).strip() != ""
            ],
            knowledge_gap_suggestions=[
                str(item)
                for item in _safe_load_json_list(row["knowledge_gap_suggestions_json"])
                if str(item).strip() != ""
            ],
            new_candidate_count=int(row["new_candidate_count"] or 0),
            approved_candidate_count=int(row["approved_candidate_count"] or 0),
            graph_trace=self._parse_graph_trace(row["graph_trace_json"]),
        )

    def _row_to_notification_event(self, row: sqlite3.Row) -> SupportIssueNotificationEvent:
        return SupportIssueNotificationEvent(
            id=row["id"],
            agent_id=row["agent_id"],
            record_id=row["record_id"],
            event_type=row["event_type"],
            recipient_user_id=row["recipient_user_id"] or "",
            status=row["status"],
            error_message=row["error_message"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _select_agents_query(self) -> str:
        return """
            SELECT
                a.*,
                r.status AS last_run_status,
                r.summary AS last_run_summary
            FROM support_issue_agents a
            LEFT JOIN support_issue_runs r
                ON r.id = (
                    SELECT id
                    FROM support_issue_runs
                    WHERE agent_id = a.id
                    ORDER BY started_at DESC
                    LIMIT 1
                )
        """

    def list_agents(self) -> list[SupportIssueAgentConfig]:
        with self._connect() as conn:
            rows = conn.execute(
                self._select_agents_query() + " ORDER BY COALESCE(a.last_run_at, a.updated_at) DESC, a.created_at DESC"
            ).fetchall()
        return [self._row_to_config(row) for row in rows]

    def get_agent(self, agent_id: str) -> SupportIssueAgentConfig | None:
        with self._connect() as conn:
            row = conn.execute(self._select_agents_query() + " WHERE a.id = ?", (agent_id,)).fetchone()
        return self._row_to_config(row) if row is not None else None

    def create_agent(
        self,
        *,
        name: str,
        description: str,
        enabled: bool,
        poll_interval_minutes: int,
        feishu_bitable_url: str,
        feishu_app_token: str,
        feishu_table_id: str,
        model_config: ModelConfig,
        knowledge_scope_type: str,
        knowledge_scope_id: str | None,
        question_field_name: str,
        answer_field_name: str,
        link_field_name: str,
        progress_field_name: str,
        status_field_name: str,
        module_field_name: str,
        registrant_field_name: str,
        feedback_result_field_name: str,
        feedback_final_answer_field_name: str,
        feedback_comment_field_name: str,
        confidence_field_name: str,
        hit_count_field_name: str,
        support_owner_rules: list[SupportIssueOwnerRule],
        fallback_support_yht_user_id: str,
        digest_enabled: bool,
        digest_recipient_emails: list[str],
        case_review_enabled: bool,
    ) -> SupportIssueAgentConfig:
        agent_id = str(uuid4())
        now = _utc_now()
        next_run_at = now + timedelta(minutes=poll_interval_minutes) if enabled else None
        next_digest_at = _next_digest_at(now) if enabled and digest_enabled else None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO support_issue_agents (
                    id, name, description, enabled, poll_interval_minutes,
                    feishu_bitable_url, feishu_app_token, feishu_table_id, model_config_json,
                    knowledge_scope_type, knowledge_scope_id,
                    question_field_name, answer_field_name, link_field_name, progress_field_name, status_field_name,
                    module_field_name, registrant_field_name,
                    feedback_result_field_name, feedback_final_answer_field_name, feedback_comment_field_name,
                    confidence_field_name, hit_count_field_name,
                    support_owner_rules_json, fallback_support_yht_user_id,
                    digest_enabled, digest_recipient_emails_json, case_review_enabled,
                    created_at, updated_at, last_run_at, next_run_at, last_digest_at, next_digest_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_id,
                    name,
                    description,
                    1 if enabled else 0,
                    poll_interval_minutes,
                    feishu_bitable_url,
                    feishu_app_token,
                    feishu_table_id,
                    model_config.model_dump_json(),
                    knowledge_scope_type,
                    knowledge_scope_id,
                    question_field_name,
                    answer_field_name,
                    link_field_name,
                    progress_field_name,
                    status_field_name,
                    module_field_name,
                    registrant_field_name,
                    feedback_result_field_name,
                    feedback_final_answer_field_name,
                    feedback_comment_field_name,
                    confidence_field_name,
                    hit_count_field_name,
                    json.dumps([item.model_dump(mode="json") for item in _normalize_owner_rules(support_owner_rules)], ensure_ascii=False),
                    fallback_support_yht_user_id.strip(),
                    1 if digest_enabled else 0,
                    json.dumps(_normalize_email_list(digest_recipient_emails), ensure_ascii=False),
                    1 if case_review_enabled else 0,
                    now.isoformat(),
                    now.isoformat(),
                    None,
                    next_run_at.isoformat() if next_run_at else None,
                    None,
                    next_digest_at.isoformat() if next_digest_at else None,
                ),
            )
        created = self.get_agent(agent_id)
        assert created is not None
        return created

    def update_agent(
        self,
        agent_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        enabled: bool | None = None,
        poll_interval_minutes: int | None = None,
        feishu_bitable_url: str | None = None,
        feishu_app_token: str | None = None,
        feishu_table_id: str | None = None,
        model_config: ModelConfig | None = None,
        knowledge_scope_type: str | None = None,
        knowledge_scope_id: str | None = None,
        question_field_name: str | None = None,
        answer_field_name: str | None = None,
        link_field_name: str | None = None,
        progress_field_name: str | None = None,
        status_field_name: str | None = None,
        module_field_name: str | None = None,
        registrant_field_name: str | None = None,
        feedback_result_field_name: str | None = None,
        feedback_final_answer_field_name: str | None = None,
        feedback_comment_field_name: str | None = None,
        confidence_field_name: str | None = None,
        hit_count_field_name: str | None = None,
        support_owner_rules: list[SupportIssueOwnerRule] | None = None,
        fallback_support_yht_user_id: str | None = None,
        digest_enabled: bool | None = None,
        digest_recipient_emails: list[str] | None = None,
        case_review_enabled: bool | None = None,
    ) -> SupportIssueAgentConfig | None:
        current = self.get_agent(agent_id)
        if current is None:
            return None

        now = _utc_now()
        next_enabled = current.enabled if enabled is None else enabled
        next_poll_interval = current.poll_interval_minutes if poll_interval_minutes is None else poll_interval_minutes
        next_digest_enabled = current.digest_enabled if digest_enabled is None else digest_enabled
        next_digest_recipients = (
            current.digest_recipient_emails if digest_recipient_emails is None else _normalize_email_list(digest_recipient_emails)
        )

        next_next_run_at = current.next_run_at
        if not next_enabled:
            next_next_run_at = None
        elif enabled is True and not current.enabled:
            next_next_run_at = now + timedelta(minutes=next_poll_interval)
        elif poll_interval_minutes is not None:
            next_next_run_at = now + timedelta(minutes=next_poll_interval)
        elif next_next_run_at is None:
            next_next_run_at = now + timedelta(minutes=next_poll_interval)

        # digest 调度和普通轮巡不同：它是固定周一 09:00，而不是“上次执行 + 间隔”。
        next_next_digest_at = current.next_digest_at
        if not next_enabled or not next_digest_enabled:
            next_next_digest_at = None
        elif digest_enabled is True and not current.digest_enabled:
            next_next_digest_at = _next_digest_at(now)
        elif enabled is True and not current.enabled:
            next_next_digest_at = _next_digest_at(now)
        elif next_next_digest_at is None:
            next_next_digest_at = _next_digest_at(now)

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE support_issue_agents
                SET name = ?, description = ?, enabled = ?, poll_interval_minutes = ?,
                    feishu_bitable_url = ?, feishu_app_token = ?, feishu_table_id = ?, model_config_json = ?,
                    knowledge_scope_type = ?, knowledge_scope_id = ?,
                    question_field_name = ?, answer_field_name = ?, link_field_name = ?, progress_field_name = ?, status_field_name = ?,
                    module_field_name = ?, registrant_field_name = ?,
                    feedback_result_field_name = ?, feedback_final_answer_field_name = ?, feedback_comment_field_name = ?,
                    confidence_field_name = ?, hit_count_field_name = ?,
                    support_owner_rules_json = ?, fallback_support_yht_user_id = ?,
                    digest_enabled = ?, digest_recipient_emails_json = ?, case_review_enabled = ?,
                    updated_at = ?, next_run_at = ?, next_digest_at = ?
                WHERE id = ?
                """,
                (
                    name if name is not None else current.name,
                    description if description is not None else current.description,
                    1 if next_enabled else 0,
                    next_poll_interval,
                    feishu_bitable_url if feishu_bitable_url is not None else current.feishu_bitable_url,
                    feishu_app_token if feishu_app_token is not None else current.feishu_app_token,
                    feishu_table_id if feishu_table_id is not None else current.feishu_table_id,
                    (model_config or current.model_settings).model_dump_json(),
                    knowledge_scope_type if knowledge_scope_type is not None else current.knowledge_scope_type,
                    knowledge_scope_id
                    if knowledge_scope_type is not None or knowledge_scope_id is not None
                    else current.knowledge_scope_id,
                    question_field_name if question_field_name is not None else current.question_field_name,
                    answer_field_name if answer_field_name is not None else current.answer_field_name,
                    link_field_name if link_field_name is not None else current.link_field_name,
                    progress_field_name if progress_field_name is not None else current.progress_field_name,
                    status_field_name if status_field_name is not None else current.status_field_name,
                    module_field_name if module_field_name is not None else current.module_field_name,
                    registrant_field_name if registrant_field_name is not None else current.registrant_field_name,
                    feedback_result_field_name
                    if feedback_result_field_name is not None
                    else current.feedback_result_field_name,
                    feedback_final_answer_field_name
                    if feedback_final_answer_field_name is not None
                    else current.feedback_final_answer_field_name,
                    feedback_comment_field_name
                    if feedback_comment_field_name is not None
                    else current.feedback_comment_field_name,
                    confidence_field_name if confidence_field_name is not None else current.confidence_field_name,
                    hit_count_field_name if hit_count_field_name is not None else current.hit_count_field_name,
                    json.dumps(
                        [
                            item.model_dump(mode="json")
                            for item in _normalize_owner_rules(
                                current.support_owner_rules if support_owner_rules is None else support_owner_rules
                            )
                        ],
                        ensure_ascii=False,
                    ),
                    (
                        current.fallback_support_yht_user_id
                        if fallback_support_yht_user_id is None
                        else fallback_support_yht_user_id.strip()
                    ),
                    1 if next_digest_enabled else 0,
                    json.dumps(next_digest_recipients, ensure_ascii=False),
                    1
                    if (current.case_review_enabled if case_review_enabled is None else case_review_enabled)
                    else 0,
                    now.isoformat(),
                    next_next_run_at.isoformat() if next_next_run_at else None,
                    next_next_digest_at.isoformat() if next_next_digest_at else None,
                    agent_id,
                ),
            )
        return self.get_agent(agent_id)

    def list_due_agents(self, current_time: datetime | None = None) -> list[SupportIssueAgentConfig]:
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

    def list_due_digest_agents(self, current_time: datetime | None = None) -> list[SupportIssueAgentConfig]:
        now = (current_time or _utc_now()).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                self._select_agents_query()
                + """
                WHERE a.enabled = 1
                  AND a.digest_enabled = 1
                  AND a.next_digest_at IS NOT NULL
                  AND a.next_digest_at <= ?
                ORDER BY a.next_digest_at ASC
                """,
                (now,),
            ).fetchall()
        return [self._row_to_config(row) for row in rows]

    def list_runs(self, agent_id: str) -> list[SupportIssueRun]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM support_issue_runs WHERE agent_id = ? ORDER BY started_at DESC",
                (agent_id,),
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def record_run(self, agent_id: str, run: SupportIssueRun) -> None:
        agent = self.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"Support issue agent not found: {agent_id}")
        ended_at = run.ended_at or _utc_now()
        next_run_at = ended_at + timedelta(minutes=agent.poll_interval_minutes) if agent.enabled else None

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO support_issue_runs (
                    id, agent_id, status, started_at, ended_at,
                    fetched_row_count, processed_row_count, generated_count,
                    manual_review_count, no_hit_count, failed_count, summary, error_message, row_results_json,
                    graph_trace_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    agent_id,
                    run.status,
                    run.started_at.isoformat(),
                    ended_at.isoformat(),
                    run.fetched_row_count,
                    run.processed_row_count,
                    run.generated_count,
                    run.manual_review_count,
                    run.no_hit_count,
                    run.failed_count,
                    run.summary,
                    run.error_message,
                    json.dumps([item.model_dump(mode="json") for item in run.row_results], ensure_ascii=False),
                    json.dumps([item.model_dump(mode="json") for item in run.graph_trace], ensure_ascii=False),
                ),
            )
            conn.execute(
                """
                UPDATE support_issue_agents
                SET last_run_at = ?, next_run_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (ended_at.isoformat(), next_run_at.isoformat() if next_run_at else None, ended_at.isoformat(), agent_id),
            )

    def list_feedback_facts(self, agent_id: str) -> list[SupportIssueFeedbackFact]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM support_issue_feedback_facts
                WHERE agent_id = ?
                ORDER BY updated_at DESC, created_at DESC
                """,
                (agent_id,),
            ).fetchall()
        return [self._row_to_feedback_fact(row) for row in rows]

    def get_feedback_fact(self, agent_id: str, record_id: str) -> SupportIssueFeedbackFact | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM support_issue_feedback_facts
                WHERE agent_id = ? AND record_id = ?
                """,
                (agent_id, record_id),
            ).fetchone()
        return self._row_to_feedback_fact(row) if row is not None else None

    def upsert_feedback_fact(self, fact: SupportIssueFeedbackFact) -> SupportIssueFeedbackFact:
        """按 `(agent_id, record_id)` 写入最新反馈事实。

        这张表只保存“当前最新值”，历史变化通过 `support_issue_feedback_history` 单独追踪。
        """

        current = self.get_feedback_fact(fact.agent_id, fact.record_id)
        created_at = current.created_at if current is not None else fact.created_at
        fact_id = current.id if current is not None else fact.id

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO support_issue_feedback_facts (
                    id, agent_id, record_id, question, progress_value, ai_solution,
                    related_links_json, feedback_result, feedback_final_answer, feedback_comment,
                    confidence_score, retrieval_hit_count, question_category, source_bitable_url,
                    created_at, updated_at, last_synced_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id, record_id)
                DO UPDATE SET
                    question = excluded.question,
                    progress_value = excluded.progress_value,
                    ai_solution = excluded.ai_solution,
                    related_links_json = excluded.related_links_json,
                    feedback_result = excluded.feedback_result,
                    feedback_final_answer = excluded.feedback_final_answer,
                    feedback_comment = excluded.feedback_comment,
                    confidence_score = excluded.confidence_score,
                    retrieval_hit_count = excluded.retrieval_hit_count,
                    question_category = excluded.question_category,
                    source_bitable_url = excluded.source_bitable_url,
                    updated_at = excluded.updated_at,
                    last_synced_at = excluded.last_synced_at
                """,
                (
                    fact_id,
                    fact.agent_id,
                    fact.record_id,
                    fact.question,
                    fact.progress_value,
                    fact.ai_solution,
                    json.dumps(fact.related_links, ensure_ascii=False),
                    fact.feedback_result,
                    fact.feedback_final_answer,
                    fact.feedback_comment,
                    fact.confidence_score,
                    fact.retrieval_hit_count,
                    fact.question_category,
                    fact.source_bitable_url,
                    created_at.isoformat(),
                    fact.updated_at.isoformat(),
                    fact.last_synced_at.isoformat(),
                ),
            )
        updated = self.get_feedback_fact(fact.agent_id, fact.record_id)
        assert updated is not None
        return updated

    def append_feedback_history(
        self,
        *,
        agent_id: str,
        record_id: str,
        changed_fields: list[str],
        previous_snapshot: dict[str, object],
        current_snapshot: dict[str, object],
        changed_at: datetime | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO support_issue_feedback_history (
                    id, agent_id, record_id, changed_at, changed_fields_json,
                    previous_snapshot_json, current_snapshot_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    agent_id,
                    record_id,
                    (changed_at or _utc_now()).isoformat(),
                    json.dumps(changed_fields, ensure_ascii=False),
                    json.dumps(previous_snapshot, ensure_ascii=False),
                    json.dumps(current_snapshot, ensure_ascii=False),
                ),
            )

    def list_case_candidates(
        self,
        agent_id: str,
        *,
        status: str | None = None,
        category: str | None = None,
        keyword: str | None = None,
    ) -> list[SupportIssueCaseCandidate]:
        """列出案例候选，并支持轻量筛选。

        这里仍然返回完整候选对象，而不是专门做一个列表 DTO，原因有两个：
        - 候选页表格与右侧详情区共用同一份数据模型；
        - 当前数据量不大，保持接口简单更利于前后端同步演进。
        """

        where_sql = ["agent_id = ?"]
        params: list[str] = [agent_id]

        normalized_status = (status or "").strip()
        if normalized_status != "":
            where_sql.append("status = ?")
            params.append(normalized_status)

        normalized_category = (category or "").strip()
        if normalized_category != "":
            where_sql.append("question_category = ?")
            params.append(normalized_category)

        normalized_keyword = (keyword or "").strip()
        if normalized_keyword != "":
            like_value = f"%{normalized_keyword}%"
            where_sql.append(
                "(question LIKE ? COLLATE NOCASE OR record_id LIKE ? COLLATE NOCASE OR final_solution LIKE ? COLLATE NOCASE)"
            )
            params.extend([like_value, like_value, like_value])

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM support_issue_case_candidates
                WHERE {" AND ".join(where_sql)}
                ORDER BY updated_at DESC, created_at DESC
                """,
                params,
            ).fetchall()
        return [self._row_to_case_candidate(row) for row in rows]

    def list_approved_case_candidates(self, agent_id: str) -> list[SupportIssueCaseCandidate]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM support_issue_case_candidates
                WHERE agent_id = ? AND status = 'approved'
                ORDER BY updated_at DESC, created_at DESC
                """,
                (agent_id,),
            ).fetchall()
        return [self._row_to_case_candidate(row) for row in rows]

    def get_case_candidate(self, candidate_id: str) -> SupportIssueCaseCandidate | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM support_issue_case_candidates WHERE id = ?",
                (candidate_id,),
            ).fetchone()
        return self._row_to_case_candidate(row) if row is not None else None

    def get_case_candidate_by_record(self, agent_id: str, record_id: str) -> SupportIssueCaseCandidate | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM support_issue_case_candidates
                WHERE agent_id = ? AND record_id = ?
                """,
                (agent_id, record_id),
            ).fetchone()
        return self._row_to_case_candidate(row) if row is not None else None

    def upsert_case_candidate(
        self,
        candidate: SupportIssueCaseCandidate,
        *,
        reset_to_pending_review: bool,
    ) -> SupportIssueCaseCandidate:
        """写入或刷新候选案例。

        `reset_to_pending_review=True` 代表候选内容被人工再次修改过，
        已有审核结论不再可靠，需要重新回到待审核状态。
        """

        current = self.get_case_candidate_by_record(candidate.agent_id, candidate.record_id)
        now = candidate.updated_at
        candidate_id = current.id if current is not None else candidate.id
        created_at = current.created_at if current is not None else candidate.created_at

        next_status = candidate.status
        next_review_comment = candidate.review_comment
        next_knowledge_document_id = candidate.knowledge_document_id
        next_approved_at = candidate.approved_at
        next_approved_by = candidate.approved_by

        if reset_to_pending_review:
            next_status = "pending_review"
            next_review_comment = ""
            next_knowledge_document_id = None
            next_approved_at = None
            next_approved_by = None
        elif current is not None:
            next_status = current.status
            next_review_comment = current.review_comment
            next_knowledge_document_id = current.knowledge_document_id
            next_approved_at = current.approved_at
            next_approved_by = current.approved_by

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO support_issue_case_candidates (
                    id, agent_id, record_id, status, question, ai_draft,
                    feedback_result, final_solution, feedback_comment, confidence_score,
                    retrieval_hit_count, question_category, related_links_json, source_bitable_url,
                    review_comment, knowledge_document_id, approved_at, approved_by,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id, record_id)
                DO UPDATE SET
                    status = excluded.status,
                    question = excluded.question,
                    ai_draft = excluded.ai_draft,
                    feedback_result = excluded.feedback_result,
                    final_solution = excluded.final_solution,
                    feedback_comment = excluded.feedback_comment,
                    confidence_score = excluded.confidence_score,
                    retrieval_hit_count = excluded.retrieval_hit_count,
                    question_category = excluded.question_category,
                    related_links_json = excluded.related_links_json,
                    source_bitable_url = excluded.source_bitable_url,
                    review_comment = excluded.review_comment,
                    knowledge_document_id = excluded.knowledge_document_id,
                    approved_at = excluded.approved_at,
                    approved_by = excluded.approved_by,
                    updated_at = excluded.updated_at
                """,
                (
                    candidate_id,
                    candidate.agent_id,
                    candidate.record_id,
                    next_status,
                    candidate.question,
                    candidate.ai_draft,
                    candidate.feedback_result,
                    candidate.final_solution,
                    candidate.feedback_comment,
                    candidate.confidence_score,
                    candidate.retrieval_hit_count,
                    candidate.question_category,
                    json.dumps(candidate.related_links, ensure_ascii=False),
                    candidate.source_bitable_url,
                    next_review_comment,
                    next_knowledge_document_id,
                    next_approved_at.isoformat() if next_approved_at else None,
                    next_approved_by,
                    created_at.isoformat(),
                    now.isoformat(),
                ),
            )
        updated = self.get_case_candidate_by_record(candidate.agent_id, candidate.record_id)
        assert updated is not None
        return updated

    def update_case_candidate_review(
        self,
        *,
        candidate_id: str,
        status: str,
        review_comment: str,
        approved_by: str | None = None,
        approved_at: datetime | None = None,
        knowledge_document_id: str | None = None,
    ) -> SupportIssueCaseCandidate | None:
        current = self.get_case_candidate(candidate_id)
        if current is None:
            return None

        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE support_issue_case_candidates
                SET status = ?,
                    review_comment = ?,
                    approved_by = ?,
                    approved_at = ?,
                    knowledge_document_id = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    review_comment,
                    approved_by,
                    approved_at.isoformat() if approved_at else None,
                    knowledge_document_id,
                    now.isoformat(),
                    candidate_id,
                ),
            )
        return self.get_case_candidate(candidate_id)

    def list_digest_runs(self, agent_id: str) -> list[SupportIssueDigestRun]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM support_issue_digest_runs
                WHERE agent_id = ?
                ORDER BY started_at DESC
                """,
                (agent_id,),
            ).fetchall()
        return [self._row_to_digest_run(row) for row in rows]

    def record_notification_event(self, event: SupportIssueNotificationEvent) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO support_issue_notification_events (
                    id, agent_id, record_id, event_type, recipient_user_id, status, error_message, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.agent_id,
                    event.record_id,
                    event.event_type,
                    event.recipient_user_id,
                    event.status,
                    event.error_message,
                    event.created_at.isoformat(),
                ),
            )

    def has_notification_event(
        self,
        *,
        agent_id: str,
        record_id: str,
        event_type: str,
        statuses: tuple[str, ...] | None = None,
    ) -> bool:
        status_clause = ""
        params: list[str] = [agent_id, record_id, event_type]
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            status_clause = f" AND status IN ({placeholders})"
            params.extend(statuses)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT 1
                FROM support_issue_notification_events
                WHERE agent_id = ? AND record_id = ? AND event_type = ?
                {status_clause}
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
        return row is not None

    def has_notification_event_for_recipient(
        self,
        *,
        agent_id: str,
        record_id: str,
        event_type: str,
        recipient_user_id: str,
        statuses: tuple[str, ...] | None = None,
    ) -> bool:
        status_clause = ""
        params: list[str] = [agent_id, record_id, event_type, recipient_user_id]
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            status_clause = f" AND status IN ({placeholders})"
            params.extend(statuses)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT 1
                FROM support_issue_notification_events
                WHERE agent_id = ? AND record_id = ? AND event_type = ? AND recipient_user_id = ?
                {status_clause}
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
        return row is not None

    def record_digest_run(
        self,
        *,
        agent_id: str,
        run: SupportIssueDigestRun,
        items: list[dict[str, object]] | None = None,
    ) -> None:
        agent = self.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"Support issue agent not found: {agent_id}")

        ended_at = run.ended_at or _utc_now()
        next_digest_at = _next_digest_after_completed_run(ended_at) if agent.enabled and agent.digest_enabled else None

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO support_issue_digest_runs (
                    id, agent_id, status, trigger_source, started_at, ended_at, period_start, period_end,
                    recipient_emails_json, email_sent, email_subject, summary, error_message,
                    total_processed_count, generated_count, manual_review_count, no_hit_count, failed_count,
                    acceptance_count, revised_acceptance_count, rejected_count,
                    acceptance_rate, rejection_rate, low_confidence_rate, no_hit_rate, manual_rewrite_rate,
                    top_categories_json, top_no_hit_topics_json, highlight_samples_json, knowledge_gap_suggestions_json,
                    new_candidate_count, approved_candidate_count, graph_trace_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    agent_id,
                    run.status,
                    run.trigger_source,
                    run.started_at.isoformat(),
                    ended_at.isoformat(),
                    run.period_start.isoformat(),
                    run.period_end.isoformat(),
                    json.dumps(run.recipient_emails, ensure_ascii=False),
                    1 if run.email_sent else 0,
                    run.email_subject,
                    run.summary,
                    run.error_message,
                    run.total_processed_count,
                    run.generated_count,
                    run.manual_review_count,
                    run.no_hit_count,
                    run.failed_count,
                    run.acceptance_count,
                    run.revised_acceptance_count,
                    run.rejected_count,
                    run.acceptance_rate,
                    run.rejection_rate,
                    run.low_confidence_rate,
                    run.no_hit_rate,
                    run.manual_rewrite_rate,
                    json.dumps([item.model_dump(mode="json") for item in run.top_categories], ensure_ascii=False),
                    json.dumps(run.top_no_hit_topics, ensure_ascii=False),
                    json.dumps(run.highlight_samples, ensure_ascii=False),
                    json.dumps(run.knowledge_gap_suggestions, ensure_ascii=False),
                    run.new_candidate_count,
                    run.approved_candidate_count,
                    json.dumps([item.model_dump(mode="json") for item in run.graph_trace], ensure_ascii=False),
                ),
            )

            for item in items or []:
                conn.execute(
                    """
                    INSERT INTO support_issue_digest_items (
                        id, digest_run_id, agent_id, record_id, candidate_id, item_type, title, payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        run.id,
                        agent_id,
                        str(item.get("record_id") or "") or None,
                        str(item.get("candidate_id") or "") or None,
                        str(item.get("item_type") or "").strip() or "unknown",
                        str(item.get("title") or "").strip(),
                        json.dumps(item.get("payload") if isinstance(item.get("payload"), dict) else {}, ensure_ascii=False),
                        ended_at.isoformat(),
                    ),
                )

            conn.execute(
                """
                UPDATE support_issue_agents
                SET last_digest_at = ?, next_digest_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    ended_at.isoformat(),
                    next_digest_at.isoformat() if next_digest_at else None,
                    ended_at.isoformat(),
                    agent_id,
                ),
            )

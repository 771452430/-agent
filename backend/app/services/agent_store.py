"""配置型 Agent 的持久化存储。

这个模块故意保持“简单但完整”：
- 不做复杂编排器，只保存 Agent 配置；
- 把模型、技能、知识范围都落到 SQLite；
- 让你能清楚看到“我的 Agent”本质上是一组可复用运行参数。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..schemas import AgentConfig, ModelConfig, ScopeType


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AgentStore:
    """负责 Agent 的增删改查。"""

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
                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    system_prompt TEXT NOT NULL,
                    model_config_json TEXT NOT NULL,
                    enabled_skills_json TEXT NOT NULL,
                    knowledge_scope_type TEXT NOT NULL,
                    knowledge_scope_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _row_to_agent(self, row: sqlite3.Row) -> AgentConfig:
        return AgentConfig(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            system_prompt=row["system_prompt"] or "",
            model_settings=ModelConfig.model_validate_json(row["model_config_json"]),
            enabled_skills=json.loads(row["enabled_skills_json"] or "[]"),
            knowledge_scope_type=row["knowledge_scope_type"],
            knowledge_scope_id=row["knowledge_scope_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def list_agents(self) -> list[AgentConfig]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM agents ORDER BY updated_at DESC, created_at DESC").fetchall()
        return [self._row_to_agent(row) for row in rows]

    def get_agent(self, agent_id: str) -> AgentConfig | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        return self._row_to_agent(row) if row else None

    def create_agent(
        self,
        *,
        name: str,
        description: str,
        system_prompt: str,
        model_config: ModelConfig,
        enabled_skills: list[str],
        knowledge_scope_type: ScopeType,
        knowledge_scope_id: str | None,
    ) -> AgentConfig:
        agent_id = str(uuid4())
        now = _utc_now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agents (
                    id, name, description, system_prompt, model_config_json,
                    enabled_skills_json, knowledge_scope_type, knowledge_scope_id,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_id,
                    name,
                    description,
                    system_prompt,
                    model_config.model_dump_json(),
                    json.dumps(enabled_skills, ensure_ascii=False),
                    knowledge_scope_type,
                    knowledge_scope_id,
                    now,
                    now,
                ),
            )
        agent = self.get_agent(agent_id)
        assert agent is not None
        return agent

    def update_agent(
        self,
        agent_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        system_prompt: str | None = None,
        model_config: ModelConfig | None = None,
        enabled_skills: list[str] | None = None,
        knowledge_scope_type: ScopeType | None = None,
        knowledge_scope_id: str | None = None,
    ) -> AgentConfig | None:
        current = self.get_agent(agent_id)
        if current is None:
            return None

        now = _utc_now().isoformat()
        next_agent = AgentConfig(
            id=current.id,
            name=name if name is not None else current.name,
            description=description if description is not None else current.description,
            system_prompt=system_prompt if system_prompt is not None else current.system_prompt,
            model_settings=model_config or current.model_settings,
            enabled_skills=enabled_skills if enabled_skills is not None else current.enabled_skills,
            knowledge_scope_type=knowledge_scope_type or current.knowledge_scope_type,
            knowledge_scope_id=knowledge_scope_id if knowledge_scope_type is not None or knowledge_scope_id is not None else current.knowledge_scope_id,
            created_at=current.created_at,
            updated_at=datetime.fromisoformat(now),
        )

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE agents
                SET name = ?, description = ?, system_prompt = ?, model_config_json = ?,
                    enabled_skills_json = ?, knowledge_scope_type = ?, knowledge_scope_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    next_agent.name,
                    next_agent.description,
                    next_agent.system_prompt,
                    next_agent.model_settings.model_dump_json(),
                    json.dumps(next_agent.enabled_skills, ensure_ascii=False),
                    next_agent.knowledge_scope_type,
                    next_agent.knowledge_scope_id,
                    now,
                    agent_id,
                ),
            )

        return self.get_agent(agent_id)

"""SQLite 线程存储。

这里故意不把“记忆”做成魔法对象，而是显式把 thread / message / tool_event
存进 SQLite。这样做有两个学习价值：
1. 你能看到 LangGraph 之外，业务层如何保存长期状态；
2. 你会更容易理解“Memory 的本质是状态管理，而不是神秘封装”。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..schemas import ChatMessage, FinalResponse, ModelConfig, ThreadState, ThreadSummary, ToolEvent


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ThreadStore:
    """负责线程、消息、工具轨迹的持久化。"""

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
                CREATE TABLE IF NOT EXISTS threads (
                    thread_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    model_config_json TEXT NOT NULL,
                    enabled_skills_json TEXT NOT NULL,
                    final_output_json TEXT
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tool_events (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    output_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    note TEXT
                );
                """
            )

    def create_thread(self, title: str, model_config: ModelConfig, enabled_skills: list[str]) -> str:
        thread_id = str(uuid4())
        now = _utc_now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO threads (thread_id, title, created_at, updated_at, model_config_json, enabled_skills_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    title,
                    now,
                    now,
                    model_config.model_dump_json(),
                    json.dumps(enabled_skills, ensure_ascii=False),
                ),
            )
        return thread_id

    def list_threads(self) -> list[ThreadSummary]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT t.thread_id, t.title, t.created_at, t.updated_at,
                       COALESCE(
                           (
                               SELECT content
                               FROM messages m
                               WHERE m.thread_id = t.thread_id
                               ORDER BY m.created_at DESC
                               LIMIT 1
                           ),
                           ''
                       ) AS last_message_preview
                FROM threads t
                ORDER BY t.updated_at DESC
                """
            ).fetchall()
        return [
            ThreadSummary(
                thread_id=row["thread_id"],
                title=row["title"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
                last_message_preview=(row["last_message_preview"] or "")[:80],
            )
            for row in rows
        ]

    def _get_thread_row(self, thread_id: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM threads WHERE thread_id = ?", (thread_id,)).fetchone()

    def update_thread_config(self, thread_id: str, model_config: ModelConfig, enabled_skills: list[str]) -> None:
        now = _utc_now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE threads
                SET updated_at = ?, model_config_json = ?, enabled_skills_json = ?
                WHERE thread_id = ?
                """,
                (now, model_config.model_dump_json(), json.dumps(enabled_skills, ensure_ascii=False), thread_id),
            )

    def append_message(self, thread_id: str, role: str, content: str) -> ChatMessage:
        message = ChatMessage(id=str(uuid4()), role=role, content=content, created_at=_utc_now())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO messages (id, thread_id, role, content, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (message.id, thread_id, role, message.content, message.created_at.isoformat()),
            )
            conn.execute("UPDATE threads SET updated_at = ? WHERE thread_id = ?", (_utc_now().isoformat(), thread_id))
        return message

    def replace_tool_events(self, thread_id: str, events: list[ToolEvent]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM tool_events WHERE thread_id = ?", (thread_id,))
            conn.executemany(
                """
                INSERT INTO tool_events (id, thread_id, tool_name, status, input_json, output_json, started_at, ended_at, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        event.id,
                        thread_id,
                        event.tool_name,
                        event.status,
                        json.dumps(event.input, ensure_ascii=False),
                        json.dumps(event.output, ensure_ascii=False),
                        event.started_at.isoformat(),
                        event.ended_at.isoformat() if event.ended_at else None,
                        event.note,
                    )
                    for event in events
                ],
            )

    def set_final_output(self, thread_id: str, final_output: FinalResponse) -> None:
        now = _utc_now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE threads
                SET final_output_json = ?, updated_at = ?
                WHERE thread_id = ?
                """,
                (final_output.model_dump_json(), now, thread_id),
            )

    def get_thread_state(self, thread_id: str) -> ThreadState | None:
        row = self._get_thread_row(thread_id)
        if row is None:
            return None
        with self._connect() as conn:
            msg_rows = conn.execute(
                "SELECT * FROM messages WHERE thread_id = ? ORDER BY created_at ASC",
                (thread_id,),
            ).fetchall()
            tool_rows = conn.execute(
                "SELECT * FROM tool_events WHERE thread_id = ? ORDER BY started_at ASC",
                (thread_id,),
            ).fetchall()

        messages = [
            ChatMessage(
                id=item["id"],
                role=item["role"],
                content=item["content"],
                created_at=datetime.fromisoformat(item["created_at"]),
            )
            for item in msg_rows
        ]
        tool_events = [
            ToolEvent(
                id=item["id"],
                tool_name=item["tool_name"],
                status=item["status"],
                input=json.loads(item["input_json"]),
                output=json.loads(item["output_json"]),
                started_at=datetime.fromisoformat(item["started_at"]),
                ended_at=datetime.fromisoformat(item["ended_at"]) if item["ended_at"] else None,
                note=item["note"],
            )
            for item in tool_rows
        ]
        final_output = FinalResponse.model_validate_json(row["final_output_json"]) if row["final_output_json"] else None
        return ThreadState(
            thread_id=thread_id,
            title=row["title"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            model_settings=ModelConfig.model_validate_json(row["model_config_json"]),
            enabled_skills=json.loads(row["enabled_skills_json"]),
            messages=messages,
            tool_events=tool_events,
            final_output=final_output,
        )

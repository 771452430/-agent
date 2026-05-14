"""Jira 重复工单审核 Agent 的内置轮巡器。"""

from __future__ import annotations

import asyncio
import logging

from .jira_duplicate_service import JiraDuplicateService


logger = logging.getLogger(__name__)


class JiraDuplicateScheduler:
    """周期性触发已启用的 Jira 重复工单审核 Agent。"""

    def __init__(self, jira_duplicate_service: JiraDuplicateService, interval_seconds: int = 15) -> None:
        self.jira_duplicate_service = jira_duplicate_service
        self.interval_seconds = max(5, interval_seconds)
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self._running_ids: set[str] = set()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._loop(), name="jira-duplicate-scheduler")

    async def stop(self) -> None:
        self._stopped.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while not self._stopped.is_set():
            try:
                due_agents = self.jira_duplicate_service.list_due_agents()
                for agent in due_agents:
                    if agent.id in self._running_ids:
                        continue
                    self._running_ids.add(agent.id)
                    asyncio.create_task(self._run_agent(agent.id))
            except Exception as exc:
                logger.exception("jira duplicate scheduler tick failed: %s", exc)
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _run_agent(self, agent_id: str) -> None:
        try:
            await asyncio.to_thread(self.jira_duplicate_service.run_agent, agent_id)
        except Exception as exc:
            logger.exception("jira duplicate agent %s run failed: %s", agent_id, exc)
        finally:
            self._running_ids.discard(agent_id)

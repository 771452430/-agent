"""Jira 历史数据同步定时器。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .jira_data_source_service import JiraDataSourceService


logger = logging.getLogger(__name__)


class JiraDataSourceScheduler:
    """按设置周期同步 Jira 历史数据。"""

    def __init__(
        self,
        jira_data_source_service: JiraDataSourceService,
        *,
        interval_seconds: int = 60,
        reindex_callback: Any | None = None,
    ) -> None:
        self.jira_data_source_service = jira_data_source_service
        self.interval_seconds = max(30, interval_seconds)
        self.reindex_callback = reindex_callback
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self._running = False

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._loop(), name="jira-data-source-scheduler")

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
                if not self._running and self.jira_data_source_service.due_for_sync():
                    self._running = True
                    asyncio.create_task(self._run_sync())
            except Exception as exc:
                logger.exception("jira data source scheduler tick failed: %s", exc)
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _run_sync(self) -> None:
        try:
            await asyncio.to_thread(
                self.jira_data_source_service.sync_now,
                reindex_callback=self.reindex_callback,
            )
        except Exception as exc:
            logger.exception("jira data source sync failed: %s", exc)
        finally:
            self._running = False

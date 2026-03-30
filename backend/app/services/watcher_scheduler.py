"""巡检 Agent 的内置轮巡器。

v1 不依赖外部 cron / Celery，而是在 FastAPI 进程内启动一个轻量扫描循环：
- 周期性检查哪些 watcher 到了 `next_run_at`；
- 命中后丢到后台线程执行；
- 用内存锁避免同一个 watcher 被并发重复触发。
"""

from __future__ import annotations

import asyncio
import logging

from .watcher_service import WatcherService


logger = logging.getLogger(__name__)


class WatcherScheduler:
    """后端内置轮巡器。"""

    def __init__(self, watcher_service: WatcherService, interval_seconds: int = 15) -> None:
        self.watcher_service = watcher_service
        self.interval_seconds = max(5, interval_seconds)
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self._running_ids: set[str] = set()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._loop(), name="watcher-scheduler")

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
                due_watchers = self.watcher_service.list_due_watchers()
                for watcher in due_watchers:
                    if watcher.id in self._running_ids:
                        continue
                    self._running_ids.add(watcher.id)
                    asyncio.create_task(self._run_watcher(watcher.id))
            except Exception as exc:
                logger.exception("watcher scheduler tick failed: %s", exc)
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _run_watcher(self, watcher_id: str) -> None:
        try:
            await asyncio.to_thread(
                self.watcher_service.run_watcher,
                watcher_id,
                force_email_snapshot=False,
                scheduled_run=True,
            )
        except Exception as exc:
            logger.exception("watcher %s run failed: %s", watcher_id, exc)
        finally:
            self._running_ids.discard(watcher_id)

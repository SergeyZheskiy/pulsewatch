import asyncio
import contextlib
import logging
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .alerts import Notifier, dispatch
from .checker import CheckResult, perform_check
from .config import Settings
from .db import utcnow
from .models import Monitor
from .service import is_due, record_check

log = logging.getLogger(__name__)


class Scheduler:
    """Single-process scheduler: every tick it finds due monitors and probes them concurrently."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        client: httpx.AsyncClient,
        notifiers: list[Notifier],
        settings: Settings,
    ):
        self.session_factory = session_factory
        self.client = client
        self.notifiers = notifiers
        self.settings = settings
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._in_flight: set[int] = set()
        self._task: asyncio.Task | None = None

    async def tick(self, now: datetime | None = None) -> int:
        """Check every due monitor once. Returns how many checks were started."""
        now = now or utcnow()
        async with self.session_factory() as session:
            monitors = (await session.scalars(select(Monitor).where(Monitor.is_active.is_(True)))).all()
        due = [m.id for m in monitors if is_due(m, now) and m.id not in self._in_flight]
        await asyncio.gather(*(self.check_monitor(mid) for mid in due))
        return len(due)

    async def check_monitor(self, monitor_id: int) -> CheckResult | None:
        """Probe one monitor now, persist the result and send alerts on a status change."""
        if monitor_id in self._in_flight:
            return None
        self._in_flight.add(monitor_id)
        try:
            async with self._semaphore, self.session_factory() as session:
                monitor = await session.get(Monitor, monitor_id)
                if monitor is None:
                    return None
                result = await perform_check(
                    self.client,
                    url=monitor.url,
                    method=monitor.method,
                    timeout=monitor.timeout_seconds,
                    expected_status=monitor.expected_status,
                    keyword=monitor.keyword,
                )
                change = await record_check(
                    session, monitor, result, now=utcnow(), failure_threshold=self.settings.failure_threshold
                )
            if change and self.notifiers:
                log.info("%s: %s -> %s", change.name, change.old, change.new)
                await dispatch(self.notifiers, change)
            return result
        finally:
            self._in_flight.discard(monitor_id)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # keep the loop alive no matter what one tick does
                log.exception("scheduler tick failed")
            await asyncio.sleep(self.settings.tick_seconds)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run_forever(), name="pulsewatch-scheduler")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

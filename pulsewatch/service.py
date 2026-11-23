from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .checker import CheckResult
from .models import Check, Incident, Monitor


@dataclass(frozen=True, slots=True)
class StatusChange:
    monitor_id: int
    name: str
    url: str
    old: str
    new: str
    at: datetime
    error: str | None = None
    down_for: timedelta | None = None


def next_status(current: str, ok: bool, consecutive_failures: int, threshold: int) -> str:
    """State machine: one success means UP, but DOWN needs `threshold` failures in a row."""
    if ok:
        return "up"
    if consecutive_failures >= threshold:
        return "down"
    return current


async def record_check(
    session: AsyncSession,
    monitor: Monitor,
    result: CheckResult,
    *,
    now: datetime,
    failure_threshold: int,
) -> StatusChange | None:
    """Persist a check, update monitor state, open/close incidents. Returns a change worth alerting on."""
    session.add(
        Check(
            monitor_id=monitor.id,
            checked_at=now,
            ok=result.ok,
            status_code=result.status_code,
            latency_ms=result.latency_ms,
            error=result.error,
        )
    )
    monitor.last_checked_at = now
    monitor.consecutive_failures = 0 if result.ok else monitor.consecutive_failures + 1

    old = monitor.status
    new = next_status(old, result.ok, monitor.consecutive_failures, failure_threshold)
    change: StatusChange | None = None

    if new != old:
        monitor.status = new
        down_for = None
        if new == "down":
            session.add(Incident(monitor_id=monitor.id, started_at=now, cause=result.error))
        elif old == "down":
            incident = await session.scalar(
                select(Incident)
                .where(Incident.monitor_id == monitor.id, Incident.resolved_at.is_(None))
                .order_by(Incident.started_at.desc())
            )
            if incident:
                incident.resolved_at = now
                down_for = now - incident.started_at
        # unknown -> up happens on every fresh start; that is not news.
        if not (old == "unknown" and new == "up"):
            change = StatusChange(monitor.id, monitor.name, monitor.url, old, new, now, result.error, down_for)

    await session.commit()
    return change


def is_due(monitor: Monitor, now: datetime) -> bool:
    if not monitor.is_active:
        return False
    if monitor.last_checked_at is None:
        return True
    return monitor.last_checked_at + timedelta(seconds=monitor.interval_seconds) <= now

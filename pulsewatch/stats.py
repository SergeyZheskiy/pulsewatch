import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Check, Incident


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile (what most monitoring tools report)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


@dataclass(frozen=True, slots=True)
class WindowStats:
    checks: int
    uptime_pct: float | None
    avg_latency_ms: float | None
    p95_latency_ms: float | None


async def window_stats(session: AsyncSession, monitor_id: int, since: datetime) -> WindowStats:
    rows = (
        await session.execute(
            select(Check.ok, Check.latency_ms).where(Check.monitor_id == monitor_id, Check.checked_at >= since)
        )
    ).all()
    if not rows:
        return WindowStats(0, None, None, None)
    ok = sum(1 for r in rows if r.ok)
    latencies = [r.latency_ms for r in rows if r.ok and r.latency_ms is not None]
    return WindowStats(
        checks=len(rows),
        uptime_pct=round(ok / len(rows) * 100, 2),
        avg_latency_ms=round(sum(latencies) / len(latencies), 1) if latencies else None,
        p95_latency_ms=percentile(latencies, 95),
    )


@dataclass(frozen=True, slots=True)
class DayBar:
    day: date
    checks: int
    uptime_pct: float | None

    @property
    def level(self) -> str:
        if self.uptime_pct is None:
            return "none"
        if self.uptime_pct >= 99.5:
            return "good"
        if self.uptime_pct >= 95:
            return "warn"
        return "bad"


async def daily_bars(session: AsyncSession, monitor_id: int, today: date, days: int = 30) -> list[DayBar]:
    """Uptime per calendar day (UTC), aggregated in SQL, oldest first, with gaps filled as 'no data'."""
    start = today - timedelta(days=days - 1)
    day_col = func.date(Check.checked_at)
    rows = (
        await session.execute(
            select(day_col, func.count(), func.sum(cast(Check.ok, Integer)))
            .where(Check.monitor_id == monitor_id, Check.checked_at >= datetime.combine(start, datetime.min.time()))
            .group_by(day_col)
        )
    ).all()
    by_day = {str(d): (n, ok or 0) for d, n, ok in rows}
    bars = []
    for i in range(days):
        d = start + timedelta(days=i)
        n, ok = by_day.get(d.isoformat(), (0, 0))
        bars.append(DayBar(d, n, round(ok / n * 100, 2) if n else None))
    return bars


async def recent_incidents(session: AsyncSession, monitor_ids: list[int], limit: int = 10) -> list[Incident]:
    if not monitor_ids:
        return []
    return list(
        (
            await session.scalars(
                select(Incident)
                .where(Incident.monitor_id.in_(monitor_ids))
                .order_by(Incident.started_at.desc())
                .limit(limit)
            )
        ).all()
    )

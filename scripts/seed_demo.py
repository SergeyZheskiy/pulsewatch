"""Fill a database with 30 days of synthetic history so the status page has something to show.

python scripts/seed_demo.py            # writes ./pulsewatch.db
PULSEWATCH_DATABASE_URL=... python scripts/seed_demo.py
"""

import asyncio
import random
from datetime import timedelta

from pulsewatch.config import get_settings
from pulsewatch.db import init_db, make_engine, make_session_factory, utcnow
from pulsewatch.models import Check, Incident, Monitor

MONITORS = [
    # name, url, base latency ms, outages as (days ago, duration minutes)
    ("Website", "https://example.com/", 120, []),
    ("Public API", "https://example.org/api/health", 85, [(12, 38)]),
    ("Payments webhook", "https://example.net/webhooks/health", 210, [(4, 95), (21, 12)]),
    ("Docs", "https://docs.example.com/", 160, []),
]
STEP = timedelta(minutes=10)


async def main() -> None:
    random.seed(7)
    engine = make_engine(get_settings().database_url)
    await init_db(engine)
    now = utcnow().replace(second=0, microsecond=0)
    start = now - timedelta(days=30)

    async with make_session_factory(engine)() as session:
        for name, url, base, outages in MONITORS:
            monitor = Monitor(name=name, url=url, interval_seconds=60, status="up", last_checked_at=now)
            session.add(monitor)
            await session.flush()
            windows = [(now - timedelta(days=d), now - timedelta(days=d) + timedelta(minutes=m)) for d, m in outages]
            for s, e in windows:
                session.add(Incident(monitor_id=monitor.id, started_at=s, resolved_at=e, cause="Unexpected status 502"))

            t = start
            while t <= now:
                down = any(s <= t < e for s, e in windows)
                session.add(
                    Check(
                        monitor_id=monitor.id,
                        checked_at=t,
                        ok=not down,
                        status_code=502 if down else 200,
                        latency_ms=None if down else round(random.gauss(base, base * 0.15), 1),
                        error="Unexpected status 502" if down else None,
                    )
                )
                t += STEP
        await session.commit()
    await engine.dispose()
    print(f"Seeded {len(MONITORS)} monitors with 30 days of history.")


if __name__ == "__main__":
    asyncio.run(main())

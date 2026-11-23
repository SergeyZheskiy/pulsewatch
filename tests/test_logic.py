from datetime import date, datetime, timedelta

import httpx
import pytest

from pulsewatch.alerts import TelegramNotifier, WebhookNotifier, dispatch, format_message, humanize
from pulsewatch.checker import CheckResult
from pulsewatch.db import init_db, make_engine
from pulsewatch.models import Check, Monitor
from pulsewatch.schemas import is_private_host
from pulsewatch.service import StatusChange, is_due, next_status, record_check
from pulsewatch.stats import daily_bars, percentile, window_stats

from .conftest import FakeInternet

NOW = datetime(2026, 3, 10, 12, 0, 0)


@pytest.mark.parametrize(
    ("current", "ok", "failures", "expected"),
    [
        ("unknown", True, 0, "up"),
        ("down", True, 0, "up"),
        ("up", False, 1, "up"),  # a single blip is tolerated
        ("up", False, 2, "down"),
        ("unknown", False, 1, "unknown"),
        ("unknown", False, 2, "down"),
    ],
)
def test_state_machine(current, ok, failures, expected):
    assert next_status(current, ok, failures, threshold=2) == expected


def test_is_due():
    m = Monitor(interval_seconds=60, is_active=True, last_checked_at=None)
    assert is_due(m, NOW)
    m.last_checked_at = NOW - timedelta(seconds=59)
    assert not is_due(m, NOW)
    m.last_checked_at = NOW - timedelta(seconds=60)
    assert is_due(m, NOW)
    m.is_active = False
    assert not is_due(m, NOW)


@pytest.mark.parametrize(
    ("url", "private"),
    [
        ("http://localhost:8000/", True),
        ("http://127.0.0.1/", True),
        ("http://10.1.2.3/", True),
        ("http://192.168.0.1/", True),
        ("http://169.254.169.254/latest/meta-data", True),  # cloud metadata endpoint
        ("http://[::1]/", True),
        ("https://example.com/", False),
        ("https://8.8.8.8/", False),
    ],
)
def test_private_host_detection(url, private):
    assert is_private_host(url) is private


def test_percentile():
    assert percentile([], 95) is None
    assert percentile([5.0], 95) == 5.0
    assert percentile([float(i) for i in range(1, 101)], 95) == 95.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.0


def test_humanize():
    assert humanize(timedelta(seconds=0)) == "0s"
    assert humanize(timedelta(seconds=75)) == "1m 15s"
    assert humanize(timedelta(hours=3, minutes=2, seconds=9)) == "3h 2m"
    assert humanize(timedelta(days=2, hours=5)) == "2d 5h"


def test_messages():
    down = StatusChange(1, "API", "https://api.test", "up", "down", NOW, error="Timeout after 10s")
    assert format_message(down) == "🔴 API is DOWN\nhttps://api.test\nReason: Timeout after 10s"
    up = StatusChange(1, "API", "https://api.test", "down", "up", NOW, down_for=timedelta(minutes=4, seconds=2))
    assert format_message(up) == "🟢 API is back UP\nhttps://api.test\nDowntime: 4m 2s"


async def test_dispatch_isolates_a_failing_channel():
    net = FakeInternet()
    async with httpx.AsyncClient(transport=httpx.MockTransport(net)) as client:
        change = StatusChange(1, "API", "https://api.test", "up", "down", NOW, error="boom")
        await dispatch(
            [WebhookNotifier(client, "https://broken-hook.test/"), TelegramNotifier(client, "t", "42")],
            change,
        )
    assert [host for host, _ in net.sent] == ["api.telegram.org"]


async def test_record_check_opens_and_closes_incidents(session):
    m = Monitor(name="API", url="https://api.test", status="up")
    session.add(m)
    await session.commit()

    fail = CheckResult(False, 503, 12.0, "Unexpected status 503")
    assert await record_check(session, m, fail, now=NOW, failure_threshold=2) is None
    change = await record_check(session, m, fail, now=NOW + timedelta(minutes=1), failure_threshold=2)
    assert change and change.new == "down" and m.status == "down"

    ok = CheckResult(True, 200, 10.0, None)
    change = await record_check(session, m, ok, now=NOW + timedelta(minutes=6), failure_threshold=2)
    assert change and change.new == "up"
    assert change.down_for == timedelta(minutes=5)


async def test_stats_and_daily_bars(session):
    m = Monitor(name="API", url="https://api.test")
    session.add(m)
    await session.flush()
    today = date(2026, 3, 10)
    # yesterday: 3 ok + 1 failure; today: 2 ok
    for hour, ok, latency in [(1, True, 100.0), (2, True, 200.0), (3, True, 300.0), (4, False, None)]:
        session.add(Check(monitor_id=m.id, checked_at=datetime(2026, 3, 9, hour), ok=ok, latency_ms=latency))
    for hour in (1, 2):
        session.add(Check(monitor_id=m.id, checked_at=datetime(2026, 3, 10, hour), ok=True, latency_ms=50.0))
    await session.commit()

    bars = await daily_bars(session, m.id, today, days=3)
    assert [(b.day.day, b.checks, b.uptime_pct, b.level) for b in bars] == [
        (8, 0, None, "none"),
        (9, 4, 75.0, "bad"),
        (10, 2, 100.0, "good"),
    ]

    s = await window_stats(session, m.id, datetime(2026, 3, 1))
    assert s.checks == 6
    assert s.uptime_pct == 83.33
    assert s.avg_latency_ms == 140.0  # failures don't count towards latency
    assert s.p95_latency_ms == 300.0


async def test_deleting_monitor_cascades_to_history(session):
    from sqlalchemy import func, select

    m = Monitor(name="API", url="https://api.test")
    session.add(m)
    await session.flush()
    session.add(Check(monitor_id=m.id, checked_at=NOW, ok=True))
    await session.commit()
    await session.delete(m)
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(Check)) == 0


async def test_sqlite_parent_folder_is_created(tmp_path):
    path = tmp_path / "data" / "pulsewatch.db"
    engine = make_engine(f"sqlite+aiosqlite:///{path}")
    await init_db(engine)
    await engine.dispose()
    assert path.exists()

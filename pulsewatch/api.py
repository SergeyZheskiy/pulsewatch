from dataclasses import asdict
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select

from .db import utcnow
from .deps import SchedulerDep, SessionDep, SettingsDep, require_api_key
from .models import Check, Incident, Monitor
from .schemas import (
    CheckOut,
    IncidentOut,
    MonitorCreate,
    MonitorOut,
    MonitorUpdate,
    PublicMonitor,
    StatsOut,
    is_private_host,
)
from .stats import window_stats

public = APIRouter(prefix="/api", tags=["public"])
router = APIRouter(prefix="/api/monitors", tags=["monitors"], dependencies=[Depends(require_api_key)])


async def get_monitor_or_404(session: SessionDep, monitor_id: int) -> Monitor:
    monitor = await session.get(Monitor, monitor_id)
    if monitor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Monitor not found")
    return monitor


def check_target(url: str, settings: SettingsDep) -> None:
    if not settings.allow_private_targets and is_private_host(url):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Private and loopback targets are disabled (set PULSEWATCH_ALLOW_PRIVATE_TARGETS=true to allow)",
        )


@public.get("/status", response_model=list[PublicMonitor])
async def public_status(session: SessionDep):
    """Machine-readable version of the status page: public monitors only."""
    monitors = (await session.scalars(select(Monitor).where(Monitor.is_public.is_(True)).order_by(Monitor.id))).all()
    since = utcnow() - timedelta(hours=24)
    return [
        PublicMonitor(
            id=m.id, name=m.name, status=m.status, uptime_24h_pct=(await window_stats(session, m.id, since)).uptime_pct
        )
        for m in monitors
    ]


@router.get("", response_model=list[MonitorOut])
async def list_monitors(session: SessionDep):
    return (await session.scalars(select(Monitor).order_by(Monitor.id))).all()


@router.post("", response_model=MonitorOut, status_code=status.HTTP_201_CREATED)
async def create_monitor(body: MonitorCreate, session: SessionDep, settings: SettingsDep):
    data = body.model_dump()
    data["url"] = str(body.url)
    check_target(data["url"], settings)
    monitor = Monitor(**data)
    session.add(monitor)
    await session.commit()
    await session.refresh(monitor)
    return monitor


@router.get("/{monitor_id}", response_model=MonitorOut)
async def get_monitor(monitor_id: int, session: SessionDep):
    return await get_monitor_or_404(session, monitor_id)


@router.patch("/{monitor_id}", response_model=MonitorOut)
async def update_monitor(monitor_id: int, body: MonitorUpdate, session: SessionDep, settings: SettingsDep):
    monitor = await get_monitor_or_404(session, monitor_id)
    changes = body.model_dump(exclude_unset=True)
    if "url" in changes:
        changes["url"] = str(body.url)
        check_target(changes["url"], settings)
    if changes.get("keyword") and changes.get("method", monitor.method) == "HEAD":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "keyword checks need method GET")
    if "keyword" in changes:
        changes["keyword"] = changes["keyword"] or None  # "" clears the keyword
    for key, value in changes.items():
        setattr(monitor, key, value)
    await session.commit()
    await session.refresh(monitor)
    return monitor


@router.delete("/{monitor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_monitor(monitor_id: int, session: SessionDep):
    monitor = await get_monitor_or_404(session, monitor_id)
    await session.delete(monitor)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{monitor_id}/check", response_model=CheckOut)
async def check_now(monitor_id: int, session: SessionDep, scheduler: SchedulerDep):
    """Run a check immediately (handy right after creating a monitor)."""
    await get_monitor_or_404(session, monitor_id)
    if await scheduler.check_monitor(monitor_id) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A check for this monitor is already running")
    return await session.scalar(
        select(Check).where(Check.monitor_id == monitor_id).order_by(Check.checked_at.desc(), Check.id.desc())
    )


@router.get("/{monitor_id}/checks", response_model=list[CheckOut])
async def list_checks(monitor_id: int, session: SessionDep, limit: int = Query(50, ge=1, le=1000)):
    await get_monitor_or_404(session, monitor_id)
    return (
        await session.scalars(
            select(Check)
            .where(Check.monitor_id == monitor_id)
            .order_by(Check.checked_at.desc(), Check.id.desc())
            .limit(limit)
        )
    ).all()


@router.get("/{monitor_id}/stats", response_model=StatsOut)
async def monitor_stats(monitor_id: int, session: SessionDep, hours: int = Query(24, ge=1, le=24 * 90)):
    await get_monitor_or_404(session, monitor_id)
    stats = await window_stats(session, monitor_id, utcnow() - timedelta(hours=hours))
    return StatsOut(window_hours=hours, **asdict(stats))


@router.get("/{monitor_id}/incidents", response_model=list[IncidentOut])
async def list_incidents(monitor_id: int, session: SessionDep):
    await get_monitor_or_404(session, monitor_id)
    return (
        await session.scalars(
            select(Incident).where(Incident.monitor_id == monitor_id).order_by(Incident.started_at.desc())
        )
    ).all()

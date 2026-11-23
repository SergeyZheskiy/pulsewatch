from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from .alerts import humanize
from .db import utcnow
from .deps import SessionDep, SettingsDep
from .models import Monitor
from .stats import daily_bars, recent_incidents, window_stats

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["humanize"] = humanize


@router.get("/", response_class=HTMLResponse)
async def status_page(request: Request, session: SessionDep, settings: SettingsDep):
    now = utcnow()
    monitors = (
        await session.scalars(
            select(Monitor).where(Monitor.is_public.is_(True), Monitor.is_active.is_(True)).order_by(Monitor.name)
        )
    ).all()
    rows = []
    for m in monitors:
        bars = await daily_bars(session, m.id, now.date(), days=30)
        stats = await window_stats(session, m.id, now - timedelta(days=30))
        rows.append({"monitor": m, "bars": bars, "stats": stats})

    names = {m.id: m.name for m in monitors}
    incidents = await recent_incidents(session, list(names), limit=8)
    down = [m for m in monitors if m.status == "down"]
    overall = "operational" if not down else ("major" if len(down) == len(monitors) else "partial")

    return templates.TemplateResponse(
        request,
        "status.html",
        {
            "title": settings.status_page_title,
            "rows": rows,
            "overall": overall,
            "incidents": incidents,
            "names": names,
            "now": now,
        },
    )


def badge_svg(label: str, value: str, color: str) -> str:
    """Shields-style flat badge. Width is estimated (≈6.5px per char at 11px Verdana)."""
    lw, vw = 10 + int(len(label) * 6.5), 10 + int(len(value) * 6.5)
    w = lw + vw
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="20" role="img" aria-label="{label}: {value}">
<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>
<clipPath id="r"><rect width="{w}" height="20" rx="3" fill="#fff"/></clipPath>
<g clip-path="url(#r)"><rect width="{lw}" height="20" fill="#555"/><rect x="{lw}" width="{vw}" height="20" fill="{color}"/><rect width="{w}" height="20" fill="url(#s)"/></g>
<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">
<text x="{lw / 2}" y="14">{label}</text><text x="{lw + vw / 2}" y="14">{value}</text></g></svg>"""


@router.get("/badge/{monitor_id}.svg")
async def uptime_badge(monitor_id: int, session: SessionDep):
    """Embeddable uptime badge for READMEs: ![uptime](https://host/badge/1.svg)"""
    monitor = await session.get(Monitor, monitor_id)
    if monitor is None or not monitor.is_public:
        raise HTTPException(404)
    stats = await window_stats(session, monitor_id, utcnow() - timedelta(days=30))
    if stats.uptime_pct is None:
        value, color = "no data", "#9f9f9f"
    else:
        value = f"{stats.uptime_pct:.2f}%".replace(".00%", "%")
        color = "#4c1" if stats.uptime_pct >= 99.5 else "#dfb317" if stats.uptime_pct >= 95 else "#e05d44"
    return Response(
        badge_svg("uptime", value, color),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=300"},
    )

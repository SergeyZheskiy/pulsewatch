import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from . import __version__
from .alerts import build_notifiers
from .api import public, router
from .config import Settings, get_settings
from .db import init_db, make_engine, make_session_factory
from .scheduler import Scheduler
from .web import router as web_router

log = logging.getLogger("pulsewatch")


def create_app(settings: Settings | None = None, *, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """App factory. `transport` lets tests swap the network for an in-memory fake."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.api_key == "change-me":
            log.warning("PULSEWATCH_API_KEY is the default value — set a real secret before exposing the API")
        engine = make_engine(settings.database_url)
        await init_db(engine)
        client = httpx.AsyncClient(transport=transport, headers={"User-Agent": settings.user_agent})
        scheduler = Scheduler(make_session_factory(engine), client, build_notifiers(settings, client), settings)

        app.state.session_factory = scheduler.session_factory
        app.state.scheduler = scheduler
        if settings.scheduler_enabled:
            scheduler.start()
        try:
            yield
        finally:
            await scheduler.stop()
            await client.aclose()
            await engine.dispose()

    app = FastAPI(
        title="Pulsewatch",
        version=__version__,
        description="Self-hosted uptime monitoring with a public status page. "
        "Write endpoints require the `X-API-Key` header.",
        lifespan=lifespan,
    )
    app.state.settings = settings

    @app.get("/healthz", tags=["public"])
    async def healthz() -> dict:
        return {"status": "ok", "version": __version__}

    app.include_router(public)
    app.include_router(router)
    app.include_router(web_router)
    return app

import secrets
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .scheduler import Scheduler


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


def get_scheduler(request: Request) -> Scheduler:
    return request.app.state.scheduler


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="Value of PULSEWATCH_API_KEY")


def require_api_key(
    settings: Annotated[Settings, Depends(get_app_settings)],
    x_api_key: Annotated[str | None, Security(api_key_header)] = None,
) -> None:
    # compare_digest avoids leaking the key length/prefix through response timing
    if x_api_key is None or not secrets.compare_digest(x_api_key.encode(), settings.api_key.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing X-API-Key header")


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
SchedulerDep = Annotated[Scheduler, Depends(get_scheduler)]

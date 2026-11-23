import ipaddress
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, PlainSerializer, model_validator

# Timestamps are stored as naive UTC; serialise them with an explicit "Z" so clients don't guess.
UTCDateTime = Annotated[datetime, PlainSerializer(lambda d: d.isoformat(timespec="seconds") + "Z", return_type=str)]


def is_private_host(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # a hostname; resolving DNS here would be slow and still racy
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified


class MonitorBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: HttpUrl
    method: Literal["GET", "HEAD"] = "GET"
    interval_seconds: int = Field(60, ge=10, le=86_400)
    timeout_seconds: float = Field(10.0, gt=0, le=60)
    expected_status: int | None = Field(None, ge=100, le=599)
    keyword: str | None = Field(None, min_length=1, max_length=200)
    is_active: bool = True
    is_public: bool = True

    @model_validator(mode="after")
    def _keyword_needs_body(self):
        if self.keyword and self.method == "HEAD":
            raise ValueError("keyword checks need a response body; use method GET")
        return self


class MonitorCreate(MonitorBase):
    pass


class MonitorUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    url: HttpUrl | None = None
    method: Literal["GET", "HEAD"] | None = None
    interval_seconds: int | None = Field(None, ge=10, le=86_400)
    timeout_seconds: float | None = Field(None, gt=0, le=60)
    expected_status: int | None = Field(None, ge=100, le=599)
    keyword: str | None = Field(None, max_length=200)
    is_active: bool | None = None
    is_public: bool | None = None


class MonitorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    url: str
    method: str
    interval_seconds: int
    timeout_seconds: float
    expected_status: int | None
    keyword: str | None
    is_active: bool
    is_public: bool
    status: Literal["up", "down", "unknown"]
    last_checked_at: UTCDateTime | None
    created_at: UTCDateTime


class CheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    checked_at: UTCDateTime
    ok: bool
    status_code: int | None
    latency_ms: float | None
    error: str | None


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    started_at: UTCDateTime
    resolved_at: UTCDateTime | None
    cause: str | None


class StatsOut(BaseModel):
    window_hours: int
    checks: int
    uptime_pct: float | None
    avg_latency_ms: float | None
    p95_latency_ms: float | None


class PublicMonitor(BaseModel):
    id: int
    name: str
    status: str
    uptime_24h_pct: float | None

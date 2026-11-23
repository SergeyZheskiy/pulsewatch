from datetime import datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, utcnow


class Monitor(Base):
    __tablename__ = "monitors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(2048))
    method: Mapped[str] = mapped_column(String(8), default="GET")
    interval_seconds: Mapped[int] = mapped_column(default=60)
    timeout_seconds: Mapped[float] = mapped_column(default=10.0)
    # None means "any status below 400 is fine".
    expected_status: Mapped[int | None] = mapped_column(default=None)
    keyword: Mapped[str | None] = mapped_column(String(200), default=None)
    is_active: Mapped[bool] = mapped_column(default=True)
    is_public: Mapped[bool] = mapped_column(default=True)

    status: Mapped[str] = mapped_column(String(10), default="unknown")  # up | down | unknown
    consecutive_failures: Mapped[int] = mapped_column(default=0)
    last_checked_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    checks: Mapped[list["Check"]] = relationship(
        back_populates="monitor", cascade="all, delete-orphan", passive_deletes=True
    )
    incidents: Mapped[list["Incident"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)


class Check(Base):
    __tablename__ = "checks"
    __table_args__ = (Index("ix_checks_monitor_time", "monitor_id", "checked_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    monitor_id: Mapped[int] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"))
    checked_at: Mapped[datetime] = mapped_column(default=utcnow)
    ok: Mapped[bool]
    status_code: Mapped[int | None] = mapped_column(default=None)
    latency_ms: Mapped[float | None] = mapped_column(default=None)
    error: Mapped[str | None] = mapped_column(String(500), default=None)

    monitor: Mapped[Monitor] = relationship(back_populates="checks")


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    monitor_id: Mapped[int] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"), index=True)
    started_at: Mapped[datetime]
    resolved_at: Mapped[datetime | None] = mapped_column(default=None)
    cause: Mapped[str | None] = mapped_column(String(500), default=None)

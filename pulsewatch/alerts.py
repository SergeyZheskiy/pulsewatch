import asyncio
import logging
from datetime import timedelta
from typing import Protocol

import httpx

from .config import Settings
from .service import StatusChange

log = logging.getLogger(__name__)


def humanize(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    parts = []
    for unit, size in (("d", 86_400), ("h", 3_600), ("m", 60)):
        if seconds >= size:
            parts.append(f"{seconds // size}{unit}")
            seconds %= size
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts[:2])


def format_message(change: StatusChange) -> str:
    if change.new == "down":
        lines = [f"🔴 {change.name} is DOWN", change.url]
        if change.error:
            lines.append(f"Reason: {change.error}")
    else:
        lines = [f"🟢 {change.name} is back UP", change.url]
        if change.down_for is not None:
            lines.append(f"Downtime: {humanize(change.down_for)}")
    return "\n".join(lines)


class Notifier(Protocol):
    async def send(self, change: StatusChange) -> None: ...


class WebhookNotifier:
    """Generic JSON webhook. `text` and `content` make it work with Slack and Discord incoming webhooks as-is."""

    def __init__(self, client: httpx.AsyncClient, url: str):
        self.client, self.url = client, url

    async def send(self, change: StatusChange) -> None:
        text = format_message(change)
        payload = {
            "event": f"monitor.{change.new}",
            "monitor": {"id": change.monitor_id, "name": change.name, "url": change.url},
            "status": change.new,
            "previous_status": change.old,
            "error": change.error,
            "at": change.at.isoformat() + "Z",
            "text": text,
            "content": text,
        }
        response = await self.client.post(self.url, json=payload, timeout=10)
        response.raise_for_status()


class TelegramNotifier:
    def __init__(self, client: httpx.AsyncClient, token: str, chat_id: str):
        self.client, self.token, self.chat_id = client, token, chat_id

    async def send(self, change: StatusChange) -> None:
        response = await self.client.post(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            json={"chat_id": self.chat_id, "text": format_message(change), "disable_web_page_preview": True},
            timeout=10,
        )
        response.raise_for_status()


def build_notifiers(settings: Settings, client: httpx.AsyncClient) -> list[Notifier]:
    notifiers: list[Notifier] = []
    if settings.webhook_url:
        notifiers.append(WebhookNotifier(client, settings.webhook_url))
    if settings.telegram_bot_token and settings.telegram_chat_id:
        notifiers.append(TelegramNotifier(client, settings.telegram_bot_token, settings.telegram_chat_id))
    return notifiers


async def dispatch(notifiers: list[Notifier], change: StatusChange) -> None:
    """Send to every channel; one broken channel must not block the others."""
    results = await asyncio.gather(*(n.send(change) for n in notifiers), return_exceptions=True)
    for notifier, result in zip(notifiers, results, strict=True):
        if isinstance(result, Exception):
            log.warning("alert via %s failed: %s", type(notifier).__name__, result)

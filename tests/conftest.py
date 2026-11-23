import json

import httpx
import pytest
from fastapi.testclient import TestClient

from pulsewatch.config import Settings
from pulsewatch.db import init_db, make_engine, make_session_factory
from pulsewatch.main import create_app


class FakeInternet:
    """In-memory stand-in for the network, passed to httpx via MockTransport."""

    def __init__(self):
        self.flappy_status = 200
        self.sent: list[tuple[str, dict]] = []  # alerts captured as (host, json body)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "ok.test":
            return httpx.Response(200, text="<h1>hello world</h1>")
        if host == "down.test":
            return httpx.Response(503, text="maintenance")
        if host == "flappy.test":
            return httpx.Response(self.flappy_status, text="flappy")
        if host == "slow.test":
            raise httpx.ReadTimeout("timed out", request=request)
        if host == "refused.test":
            raise httpx.ConnectError("Connection refused", request=request)
        if host in ("hooks.test", "api.telegram.org"):
            self.sent.append((host, json.loads(request.content)))
            return httpx.Response(200, json={"ok": True})
        if host == "broken-hook.test":
            return httpx.Response(500)
        return httpx.Response(404)


@pytest.fixture
def net() -> FakeInternet:
    return FakeInternet()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite://",
        api_key="test-key",
        scheduler_enabled=False,
        failure_threshold=2,
        webhook_url="https://hooks.test/alert",
        telegram_bot_token="123:abc",
        telegram_chat_id="42",
    )


@pytest.fixture
def client(settings, net):
    app = create_app(settings, transport=httpx.MockTransport(net))
    with TestClient(app, headers={"X-API-Key": "test-key"}) as c:
        yield c


@pytest.fixture
def tick(client):
    """Run one scheduler tick inside the app's event loop."""
    return lambda: client.portal.call(client.app.state.scheduler.tick)


@pytest.fixture
async def session():
    engine = make_engine("sqlite+aiosqlite://")
    await init_db(engine)
    async with make_session_factory(engine)() as s:
        yield s
    await engine.dispose()

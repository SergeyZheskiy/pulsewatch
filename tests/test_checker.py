import httpx
import pytest

from pulsewatch.checker import perform_check

from .conftest import FakeInternet


@pytest.fixture
async def http():
    async with httpx.AsyncClient(transport=httpx.MockTransport(FakeInternet())) as c:
        yield c


async def test_healthy_target(http):
    r = await perform_check(http, url="https://ok.test/")
    assert r.ok and r.status_code == 200 and r.error is None
    assert r.latency_ms is not None and r.latency_ms >= 0


async def test_server_error_is_a_failure(http):
    r = await perform_check(http, url="https://down.test/")
    assert not r.ok and r.status_code == 503
    assert r.error == "Unexpected status 503"


async def test_expected_status_overrides_default_rule(http):
    assert (await perform_check(http, url="https://down.test/", expected_status=503)).ok
    assert not (await perform_check(http, url="https://ok.test/", expected_status=204)).ok


async def test_keyword(http):
    assert (await perform_check(http, url="https://ok.test/", keyword="hello")).ok
    r = await perform_check(http, url="https://ok.test/", keyword="goodbye")
    assert not r.ok and r.status_code == 200
    assert "goodbye" in r.error


async def test_timeout_and_connection_errors_never_raise(http):
    r = await perform_check(http, url="https://slow.test/", timeout=3)
    assert not r.ok and r.error == "Timeout after 3s" and r.latency_ms is None
    r = await perform_check(http, url="https://refused.test/")
    assert not r.ok and r.error.startswith("ConnectError")

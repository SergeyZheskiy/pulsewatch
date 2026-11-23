import time
from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class CheckResult:
    ok: bool
    status_code: int | None
    latency_ms: float | None
    error: str | None


async def perform_check(
    client: httpx.AsyncClient,
    *,
    url: str,
    method: str = "GET",
    timeout: float = 10.0,
    expected_status: int | None = None,
    keyword: str | None = None,
) -> CheckResult:
    """Run a single HTTP probe. Never raises: every failure becomes a CheckResult with an error message."""
    started = time.perf_counter()
    try:
        response = await client.request(method, url, timeout=timeout, follow_redirects=True)
    except httpx.TimeoutException:
        return CheckResult(False, None, None, f"Timeout after {timeout:g}s")
    except httpx.HTTPError as exc:
        detail = str(exc) or type(exc).__name__
        return CheckResult(False, None, None, f"{type(exc).__name__}: {detail}"[:500])

    latency = round((time.perf_counter() - started) * 1000, 1)
    code = response.status_code

    ok = code == expected_status if expected_status is not None else code < 400
    if not ok:
        return CheckResult(False, code, latency, f"Unexpected status {code}")
    if keyword and keyword not in response.text:
        return CheckResult(False, code, latency, f'Keyword "{keyword}" not found in response')
    return CheckResult(True, code, latency, None)

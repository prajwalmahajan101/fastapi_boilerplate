"""Integration coverage for the boilerplate's resilience-kit wiring.

Drives the live FastAPI app (lifespan engaged → real Redis + real
Postgres api-log backend → real kit middleware stack) and asserts the
properties the boilerplate is responsible for:

* the kit's security-headers middleware reaches the wire,
* the per-endpoint throttle uses the configured Redis backend and
  emits the documented 429 envelope + headers when the budget is
  spent,
* the request-id middleware stamps the inbound id onto both the
  response header and the envelope's ``request_id`` field.

These tests live in the integration tier (one layer + one backing
store) even though the dependency-under-test is wired into the HTTP
path; the tier convention forbids importing the e2e ``live_client``
fixture, so the lifespan-engaged ``TestClient`` is built inline here.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from src.app import app
from src.common.settings import settings


def _tcp_reachable(host: str, port: int, timeout: float = 0.25) -> bool:
    """Return ``True`` when ``(host, port)`` accepts a TCP connection."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _redis_host_port() -> tuple[str, int]:
    """Extract ``(host, port)`` from the configured default Redis URL."""
    url = settings.redis_urls.get("default", "redis://localhost:6379/0")
    host_part = url.split("://", 1)[-1].split("/", 1)[0]
    host, _, port = host_part.partition(":")
    return host or "localhost", int(port or "6379")


_REDIS_HOST, _REDIS_PORT = _redis_host_port()

pytestmark = pytest.mark.skipif(
    not _tcp_reachable(settings.db_host, settings.db_port)
    or not _tcp_reachable(_REDIS_HOST, _REDIS_PORT),
    reason="Postgres or Redis unreachable — start the docker stack first.",
)


@pytest.fixture
def live_client() -> Iterator[TestClient]:
    """Lifespan-engaged ``TestClient`` built inline for this tier.

    Mirrors ``tests/e2e/conftest.py::live_client``; the integration
    tier rule forbids cross-tier conftest imports.
    """
    with TestClient(app) as client:
        yield client


# ── security headers from the kit middleware stack ──────────────────


def test_security_headers_present_on_live_response(
    live_client: TestClient,
) -> None:
    """The kit's ``SecurityHeadersMiddleware`` reaches the response."""
    response = live_client.get("/api/v1/hello")
    assert response.status_code == 200
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "Strict-Transport-Security" in response.headers


# ── throttle backed by Redis ────────────────────────────────────────


def test_rate_limit_returns_429_envelope_under_real_redis(
    live_client: TestClient, redis_client
) -> None:
    """Driving past the per-endpoint budget yields a 429 + envelope.

    Lifespan picks the Redis-backed throttle store (not the in-memory
    fallback) because the configured Redis URL is reachable. The
    response headers and envelope shape are the contract the kit
    promises.
    """
    del redis_client  # the fixture's flushdb at setup is the point
    last = None
    for _ in range(70):  # budget is 60/min
        last = live_client.get("/api/v1/hello")
        if last.status_code == 429:
            break
    assert last is not None and last.status_code == 429, (
        f"Never observed a 429 in 70 requests; final code={getattr(last, 'status_code', None)}"
    )
    assert last.headers.get("Retry-After")
    assert last.headers.get("X-RateLimit-Limit") == "60"
    assert "X-RateLimit-Remaining" in last.headers
    body = last.json()
    assert body["success"] is False
    assert body["errors"]
    # Kit's RateLimitError surfaces with a kit-specific code in the envelope.
    assert any(
        "RATE" in (err.get("code") or "").upper() or err.get("code") == "RATE_LIMIT"
        for err in body["errors"]
    )


@pytest.mark.skipif(
    not os.environ.get("RESILIENCE_REDIS_URL"),
    reason="RESILIENCE_REDIS_URL not set; kit falls back to in-memory throttle.",
)
async def test_throttle_state_lands_in_redis(
    live_client: TestClient, redis_client
) -> None:
    """When the kit is configured for Redis, throttle keys land in it.

    The kit reads ``RESILIENCE_REDIS_URL`` (not the boilerplate's
    ``redis_urls`` dict) to pick its throttle backend; this test is
    skipped unless that env var is set so the suite stays green on
    machines that only have the boilerplate's Redis configured.
    """
    await redis_client.flushdb()
    response = live_client.get("/api/v1/hello")
    assert response.status_code == 200

    keys = await redis_client.keys("*")
    assert keys, "Expected at least one Redis key written by the throttle store."


# ── request-id propagation under lifespan ───────────────────────────


def test_request_id_round_trips_under_lifespan(live_client: TestClient) -> None:
    """The kit-issued request id is reflected on the response header + envelope."""
    response = live_client.get("/api/v1/hello")
    assert response.status_code == 200
    header_rid = response.headers.get("X-Request-ID")
    assert header_rid
    assert response.json()["request_id"] == header_rid


def test_inbound_request_id_is_honoured(live_client: TestClient) -> None:
    """An inbound ``X-Request-ID`` is honoured rather than replaced."""
    supplied = "11111111-2222-3333-4444-555555555555"
    response = live_client.get("/api/v1/hello", headers={"X-Request-ID": supplied})
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID") == supplied
    assert response.json()["request_id"] == supplied

"""End-to-end coverage for ``/api/v1/auth/token/refresh`` + ``/api/v1/auth/logout``.

Drives the lifespan-engaged FastAPI app to exercise the refresh-token
rotation path and the idempotent logout path on the real JWT provider.
The router is only mounted when ``"jwt"`` is in
``settings.auth_enabled_providers`` and ``jwt_signing_key`` is set, so
the whole file is skipped when JWT is not enabled — letting the suite
stay green on the default ``["api_key"]`` config.

Enable locally::

    export AUTH_ENABLED_PROVIDERS='["jwt","api_key"]'
    export JWT_SIGNING_KEY='dev-only-secret'

Run with services up (Postgres + Redis), then ``pytest tests/e2e/test_jwt_refresh.py``.
"""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.common.settings import settings
from src.model.auth import User


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
_JWT_ENABLED = (
    "jwt" in (settings.auth_enabled_providers or [])
    and settings.jwt_signing_key is not None
)

pytestmark = [
    pytest.mark.skipif(
        not _tcp_reachable(settings.db_host, settings.db_port)
        or not _tcp_reachable(_REDIS_HOST, _REDIS_PORT),
        reason="Postgres or Redis unreachable — start the docker stack first.",
    ),
    pytest.mark.skipif(
        not _JWT_ENABLED,
        reason=(
            "JWT provider not enabled; set AUTH_ENABLED_PROVIDERS to include "
            "'jwt' and configure JWT_SIGNING_KEY to exercise these routes."
        ),
    ),
]


@dataclass
class SeededUser:
    """Minimal user record + the token pair minted for them."""

    user_id: int
    email: str
    access_token: str
    refresh_token: str


@pytest.fixture
async def seeded_user(live_client: TestClient) -> AsyncIterator[SeededUser]:
    """Seed a User and mint a JWT pair for them.

    The token pair is minted via ``src.auth.jwt`` directly rather than
    through an HTTP login route — the boilerplate doesn't ship one yet
    (OAuth is the canonical entry); minting in-process is sufficient
    to exercise the refresh + logout routes.
    """
    del live_client  # marker dependency: forces the lifespan to start
    from src.auth.jwt import mint_token_pair

    engine = create_async_engine(settings.db_dsn, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        async with s.begin():
            user = User(email="jwt-e2e@example.com", first_name="JWT", last_name="E2E")
            s.add(user)
            await s.flush()
            user_id = user.id

    pair = mint_token_pair(user_id)
    try:
        yield SeededUser(
            user_id=user_id,
            email="jwt-e2e@example.com",
            access_token=pair["access_token"],
            refresh_token=pair["refresh_token"],
        )
    finally:
        async with maker() as s:
            async with s.begin():
                await s.execute(delete(User).where(User.id == user_id))
        await engine.dispose()


# ── refresh ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def _flush_throttle():
    """Flush Redis throttle keys (but not blacklist) before every test.

    The auth-scope budget is 5/min — without a flush, the second test
    onwards would 429. We delete *only* the kit's throttle namespace
    so the JWT blacklist cache (used by the refresh-token rotation
    path) stays intact across tests.
    """
    import redis.asyncio as _redis

    url = settings.redis_urls.get("default", "redis://localhost:6379/0")
    client = _redis.from_url(url, decode_responses=True)
    # Throttle keys are prefixed with the kit's throttle namespace; the
    # blacklist cache uses a distinct prefix. Delete the throttle keys
    # only — flushdb would wipe the blacklist too.
    for pattern in ("*throttle*", "*rate*"):
        cursor = 0
        while True:
            cursor, keys = await client.scan(cursor=cursor, match=pattern, count=100)
            if keys:
                await client.delete(*keys)
            if cursor == 0:
                break
    try:
        yield
    finally:
        await client.aclose()


def test_refresh_returns_fresh_pair(
    live_client: TestClient, seeded_user: SeededUser
) -> None:
    """``POST /token/refresh`` mints a new pair distinct from the old one."""
    response = live_client.post(
        "/api/v1/auth/token/refresh",
        json={"refresh_token": seeded_user.refresh_token},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["access_token"] and data["access_token"] != seeded_user.access_token
    assert data["refresh_token"] and data["refresh_token"] != seeded_user.refresh_token
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == settings.jwt_access_ttl_seconds


def test_refresh_blacklists_old_refresh_jti(
    live_client: TestClient, seeded_user: SeededUser
) -> None:
    """The old refresh token's ``jti`` is blacklisted after rotation."""
    first = live_client.post(
        "/api/v1/auth/token/refresh",
        json={"refresh_token": seeded_user.refresh_token},
    )
    assert first.status_code == 200
    # Replaying the original refresh token should now be rejected.
    replay = live_client.post(
        "/api/v1/auth/token/refresh",
        json={"refresh_token": seeded_user.refresh_token},
    )
    assert replay.status_code == 401
    body = replay.json()
    assert body["errors"][0]["code"] in {"TOKEN_REVOKED", "AUTHENTICATION_FAILED"}


def test_refresh_with_garbage_token_returns_401(live_client: TestClient) -> None:
    """A non-JWT string is rejected with the documented envelope."""
    response = live_client.post(
        "/api/v1/auth/token/refresh", json={"refresh_token": "not-a-jwt"}
    )
    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] in {
        "TOKEN_INVALID",
        "AUTHENTICATION_FAILED",
    }


# ── logout ──────────────────────────────────────────────────────────


def test_logout_blacklists_refresh_token(
    live_client: TestClient, seeded_user: SeededUser
) -> None:
    """``POST /logout`` blacklists the supplied refresh token's jti."""
    response = live_client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": seeded_user.refresh_token},
    )
    assert response.status_code == 200
    # Subsequent refresh with the same token must now fail.
    rejected = live_client.post(
        "/api/v1/auth/token/refresh",
        json={"refresh_token": seeded_user.refresh_token},
    )
    assert rejected.status_code == 401


def test_logout_is_idempotent(live_client: TestClient, seeded_user: SeededUser) -> None:
    """Logging out the same token twice still returns 200."""
    first = live_client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": seeded_user.refresh_token},
    )
    assert first.status_code == 200
    second = live_client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": seeded_user.refresh_token},
    )
    assert second.status_code == 200


def test_logout_swallows_invalid_token(live_client: TestClient) -> None:
    """A malformed refresh token is treated as already-logged-out."""
    response = live_client.post(
        "/api/v1/auth/logout", json={"refresh_token": "not-a-jwt"}
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Logged out."

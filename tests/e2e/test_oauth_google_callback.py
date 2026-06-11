"""End-to-end coverage for ``GET /api/v1/auth/google/callback``.

The OAuth callback handler verifies an upstream id-token through
Authlib, upserts a local ``User``, attaches default roles, and mints a
JWT pair. We monkeypatch the Authlib client wrapper so the test never
talks to Google — only the boilerplate's wiring is under test.

Skipped unless the OAuth provider is enabled in
``auth_enabled_providers``; enable locally with::

    export AUTH_ENABLED_PROVIDERS='["oauth_google","jwt","api_key"]'
    export JWT_SIGNING_KEY='dev-only-secret'
    export GOOGLE_OAUTH_CLIENT_ID='dev'
    export GOOGLE_OAUTH_CLIENT_SECRET='dev'
"""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.common.settings import settings
from src.model.auth import Role, User, user_roles


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
_OAUTH_ENABLED = "oauth_google" in (settings.auth_enabled_providers or [])
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
        not (_OAUTH_ENABLED and _JWT_ENABLED),
        reason=(
            "OAuth google + JWT providers not enabled; set "
            "AUTH_ENABLED_PROVIDERS to include both and configure the "
            "Google OAuth + JWT secrets to exercise this route."
        ),
    ),
]


class _FakeGoogleClient:
    """Stand-in for the ``oauth.google`` attribute Authlib exposes."""

    def __init__(self, claims: dict[str, Any]) -> None:
        self._claims = claims

    async def authorize_access_token(self, request: Any) -> dict[str, Any]:
        """Return a canned token payload with the test ``userinfo``."""
        del request
        return {"userinfo": self._claims, "access_token": "fake"}


class _FakeOAuth:
    """Fake Authlib client whose ``.google`` is the canned response."""

    def __init__(self, claims: dict[str, Any]) -> None:
        self.google = _FakeGoogleClient(claims)


def _fake_oauth(claims: dict[str, Any]) -> _FakeOAuth:
    """Build a fake OAuth client wrapping ``claims``."""
    return _FakeOAuth(claims)


@pytest.fixture(autouse=True)
async def _flush_throttle():
    """Flush Redis throttle state before every test in this file."""
    import redis.asyncio as _redis

    url = settings.redis_urls.get("default", "redis://localhost:6379/0")
    client = _redis.from_url(url, decode_responses=True)
    await client.flushdb()
    try:
        yield
    finally:
        await client.aclose()


@pytest.fixture
def patch_oauth_client(monkeypatch: pytest.MonkeyPatch):
    """Monkeypatch ``_get_oauth_client`` to return our fake."""

    def _patch(claims: dict[str, Any]) -> None:
        monkeypatch.setattr(
            "src.auth.oauth_google._get_oauth_client",
            lambda: _fake_oauth(claims),
        )

    return _patch


@pytest.fixture
async def default_role() -> AsyncIterator[int]:
    """Insert one ``is_default=True`` role so first-signin attaches it."""
    engine = create_async_engine(settings.db_dsn, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        async with s.begin():
            role = Role(
                name="e2e-oauth-default",
                description="default role for oauth e2e tests",
                is_default=True,
            )
            s.add(role)
            await s.flush()
            role_id = role.id
    try:
        yield role_id
    finally:
        async with maker() as s:
            async with s.begin():
                await s.execute(
                    delete(user_roles).where(user_roles.c.role_id == role_id)
                )
                await s.execute(delete(Role).where(Role.id == role_id))
        await engine.dispose()


@pytest.fixture
async def cleanup_users() -> AsyncIterator[set[str]]:
    """Collect emails to purge on teardown."""
    emails: set[str] = set()
    yield emails
    if not emails:
        return
    engine = create_async_engine(settings.db_dsn, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        async with s.begin():
            user_ids_q = await s.execute(select(User.id).where(User.email.in_(emails)))
            user_ids = [row[0] for row in user_ids_q.all()]
            if user_ids:
                await s.execute(
                    delete(user_roles).where(user_roles.c.user_id.in_(user_ids))
                )
                await s.execute(delete(User).where(User.id.in_(user_ids)))
    await engine.dispose()


# ── callback happy path ─────────────────────────────────────────────


def test_callback_mints_jwt_for_new_user(
    live_client: TestClient,
    patch_oauth_client,
    default_role: int,
    cleanup_users: set[str],
) -> None:
    """A first-time login creates the User, attaches default role, mints tokens."""
    del default_role  # presence is the point; cleanup handles the rest
    email = "oauth-new@example.com"
    cleanup_users.add(email)
    patch_oauth_client(
        {
            "email": email,
            "email_verified": True,
            "given_name": "First",
            "family_name": "Login",
        }
    )

    response = live_client.get("/api/v1/auth/google/callback")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["access_token"]
    assert data["refresh_token"]
    assert data["token_type"] == "bearer"


def test_callback_returning_user_does_not_duplicate(
    live_client: TestClient, patch_oauth_client, cleanup_users: set[str]
) -> None:
    """A second callback for the same email reuses the existing user row."""
    email = "oauth-returning@example.com"
    cleanup_users.add(email)
    patch_oauth_client(
        {"email": email, "email_verified": True, "given_name": "R", "family_name": "U"}
    )

    first = live_client.get("/api/v1/auth/google/callback")
    assert first.status_code == 200
    second = live_client.get("/api/v1/auth/google/callback")
    assert second.status_code == 200

    engine = create_async_engine(settings.db_dsn, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    import asyncio as _asyncio

    async def _count() -> int:
        async with maker() as s:
            rows = await s.execute(select(User).where(User.email == email))
            return len(rows.scalars().all())

    count = _asyncio.get_event_loop().run_until_complete(_count())
    _asyncio.get_event_loop().run_until_complete(engine.dispose())
    assert count == 1


# ── error paths ─────────────────────────────────────────────────────


def test_callback_unverified_email_rejected(
    live_client: TestClient, patch_oauth_client
) -> None:
    """An unverified email is rejected with the 401 envelope."""
    patch_oauth_client({"email": "unverified@example.com", "email_verified": False})
    response = live_client.get("/api/v1/auth/google/callback")
    assert response.status_code == 401
    body = response.json()
    assert body["errors"][0]["code"] == "AUTHENTICATION_FAILED"


def test_callback_upstream_failure_returns_401(
    live_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An Authlib exception during ``authorize_access_token`` collapses to 401."""

    class _BoomGoogle:
        async def authorize_access_token(self, request: Any) -> Any:
            del request
            raise RuntimeError("upstream blew up")

    class _BoomOAuth:
        google = _BoomGoogle()

    monkeypatch.setattr("src.auth.oauth_google._get_oauth_client", lambda: _BoomOAuth())

    response = live_client.get("/api/v1/auth/google/callback")
    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "AUTHENTICATION_FAILED"

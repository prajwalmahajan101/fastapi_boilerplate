"""End-to-end coverage of the auth surface.

Drives the real FastAPI app through ``live_client`` (lifespan engaged,
Postgres + Redis attached) to prove the auth provider chain, RBAC
dependency, error envelope, and api-key lifecycle all cooperate on a
live request.

Two seeded principals are reused across the file:

* ``admin`` — owns a role with ``is_superuser_role=True`` so every
  ``RequireResource`` check short-circuits to allow.
* ``nopriv`` — no roles, so any protected route returns 403.

Both principals own one pre-seeded API key whose raw secret is captured
during seed so tests can pass it as ``X-API-Key`` against the live app.
"""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.auth.api_key import generate_api_key
from src.common.settings import settings
from src.model.auth import APIKey, Role, User, user_roles


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


@dataclass
class SeededPrincipal:
    """A seeded user + their pre-issued API key (raw secret captured)."""

    user_id: int
    email: str
    api_key_id: int
    raw_key: str
    role_id: int | None = None


@dataclass
class SeededFixture:
    """Both principals + their cleanup ids."""

    admin: SeededPrincipal
    nopriv: SeededPrincipal


async def _seed_principal(
    maker, *, email: str, with_superuser_role: bool
) -> SeededPrincipal:
    """Insert a user, optionally attach a superuser role, mint one API key."""
    async with maker() as session:
        async with session.begin():
            user = User(email=email, first_name="E2E", last_name="Test")
            session.add(user)
            await session.flush()

            role_id: int | None = None
            if with_superuser_role:
                role = Role(
                    name=f"e2e-superuser-{user.id}",
                    description="e2e superuser",
                    is_superuser_role=True,
                )
                session.add(role)
                await session.flush()
                await session.execute(
                    insert(user_roles).values(user_id=user.id, role_id=role.id)
                )
                role_id = role.id

            raw_key, prefix = generate_api_key()
            api_key = APIKey(
                user_id=user.id,
                name="e2e-seed",
                prefix=prefix,
                secret=raw_key,
                is_active=True,
            )
            session.add(api_key)
            await session.flush()
            return SeededPrincipal(
                user_id=user.id,
                email=email,
                api_key_id=api_key.id,
                raw_key=raw_key,
                role_id=role_id,
            )


async def _purge_principal(maker, principal: SeededPrincipal) -> None:
    """Hard-delete the seeded user + their role (api_keys cascade)."""
    from sqlalchemy import delete

    async with maker() as session:
        async with session.begin():
            await session.execute(
                delete(user_roles).where(user_roles.c.user_id == principal.user_id)
            )
            await session.execute(delete(User).where(User.id == principal.user_id))
            if principal.role_id is not None:
                await session.execute(delete(Role).where(Role.id == principal.role_id))


@pytest.fixture
async def seeded(live_client: TestClient) -> AsyncIterator[SeededFixture]:
    """Seed admin + nopriv users with API keys; purge on teardown.

    Uses a standalone engine bound to the pytest event loop rather than
    the app engine: asyncpg connections are loop-bound, and the app
    engine is created on the ``anyio`` portal loop the ``TestClient``
    drives.
    """
    del live_client  # marker dependency, not used directly
    engine = create_async_engine(settings.db_dsn, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    admin = await _seed_principal(
        maker, email="e2e-admin@example.com", with_superuser_role=True
    )
    nopriv = await _seed_principal(
        maker, email="e2e-nopriv@example.com", with_superuser_role=False
    )
    try:
        yield SeededFixture(admin=admin, nopriv=nopriv)
    finally:
        await _purge_principal(maker, admin)
        await _purge_principal(maker, nopriv)
        await engine.dispose()


# ── /me ─────────────────────────────────────────────────────────────


def test_me_returns_authenticated_user(
    live_client: TestClient, seeded: SeededFixture
) -> None:
    """``GET /me`` with a valid X-API-Key returns the owner's profile."""
    response = live_client.get(
        "/api/v1/me", headers={"X-API-Key": seeded.admin.raw_key}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["request_id"]
    assert body["data"]["email"] == seeded.admin.email
    assert body["data"]["is_active"] is True
    assert isinstance(body["data"]["roles"], list)


def test_me_without_key_returns_401(live_client: TestClient) -> None:
    """Missing ``X-API-Key`` hits the auth-failed handler (401 envelope)."""
    response = live_client.get("/api/v1/me")
    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["errors"]
    assert body["errors"][0]["code"] in {
        "AUTHENTICATION_FAILED",
        "PERMISSION_DENIED",
    }


def test_me_with_invalid_key_returns_401(live_client: TestClient) -> None:
    """A syntactically valid but unknown key is rejected with 401."""
    response = live_client.get(
        "/api/v1/me", headers={"X-API-Key": "deadbeefcafebabe-not-a-real-key"}
    )
    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["errors"][0]["code"] == "AUTHENTICATION_FAILED"


# ── /api-keys CRUD ──────────────────────────────────────────────────


def test_list_api_keys_returns_caller_keys(
    live_client: TestClient, seeded: SeededFixture
) -> None:
    """``GET /api-keys`` returns the caller's keys (including the seed key)."""
    response = live_client.get(
        "/api/v1/api-keys", headers={"X-API-Key": seeded.admin.raw_key}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    ids = [row["id"] for row in body["data"]]
    assert seeded.admin.api_key_id in ids
    # The raw secret is never echoed back on the list endpoint.
    assert all("key" not in row for row in body["data"])


def test_create_api_key_returns_raw_secret_once(
    live_client: TestClient, seeded: SeededFixture
) -> None:
    """``POST /api-keys`` returns 201 + the raw key in the envelope."""
    response = live_client.post(
        "/api/v1/api-keys",
        headers={"X-API-Key": seeded.admin.raw_key},
        json={"name": "ci-pipeline"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["success"] is True
    assert body["data"]["name"] == "ci-pipeline"
    new_raw = body["data"]["key"]
    assert isinstance(new_raw, str) and len(new_raw) >= 32
    assert body["data"]["prefix"] == new_raw[:8]
    # The newly issued key authenticates against /me.
    follow_up = live_client.get("/api/v1/me", headers={"X-API-Key": new_raw})
    assert follow_up.status_code == 200
    assert follow_up.json()["data"]["email"] == seeded.admin.email


def test_revoke_then_reuse_is_rejected(
    live_client: TestClient, seeded: SeededFixture
) -> None:
    """Once revoked the key fails the next auth attempt with 401."""
    # Create a disposable key so we don't kill the seed key the
    # subsequent tests depend on.
    create = live_client.post(
        "/api/v1/api-keys",
        headers={"X-API-Key": seeded.admin.raw_key},
        json={"name": "throwaway"},
    )
    assert create.status_code == 201
    throwaway_id = create.json()["data"]["id"]
    throwaway_raw = create.json()["data"]["key"]

    revoke = live_client.post(
        f"/api/v1/api-keys/{throwaway_id}/revoke",
        headers={"X-API-Key": seeded.admin.raw_key},
    )
    assert revoke.status_code == 200
    assert revoke.json()["message"] == "API key revoked."

    # Re-revoke is idempotent — same 200, different message.
    second = live_client.post(
        f"/api/v1/api-keys/{throwaway_id}/revoke",
        headers={"X-API-Key": seeded.admin.raw_key},
    )
    assert second.status_code == 200
    assert second.json()["message"] == "API key was already revoked."

    # Using the revoked key against /me fails closed.
    rejected = live_client.get("/api/v1/me", headers={"X-API-Key": throwaway_raw})
    assert rejected.status_code == 401


def test_revoke_unknown_id_returns_404_envelope(
    live_client: TestClient, seeded: SeededFixture
) -> None:
    """Revoking a non-existent key returns the 404 error envelope."""
    response = live_client.post(
        "/api/v1/api-keys/99999999/revoke",
        headers={"X-API-Key": seeded.admin.raw_key},
    )
    assert response.status_code == 404, response.text
    body = response.json()
    assert body["success"] is False
    assert body["errors"]
    assert body["request_id"]


# ── RBAC ────────────────────────────────────────────────────────────


def test_rbac_denies_user_without_role(
    live_client: TestClient, seeded: SeededFixture
) -> None:
    """A user with no roles hits ``PermissionDeniedError`` (403)."""
    response = live_client.get(
        "/api/v1/me", headers={"X-API-Key": seeded.nopriv.raw_key}
    )
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["success"] is False
    assert body["errors"][0]["code"] == "PERMISSION_DENIED"


def test_rbac_denies_create_for_user_without_role(
    live_client: TestClient, seeded: SeededFixture
) -> None:
    """Without API_KEY:CREATE the create route also 403s."""
    response = live_client.post(
        "/api/v1/api-keys",
        headers={"X-API-Key": seeded.nopriv.raw_key},
        json={"name": "should-fail"},
    )
    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "PERMISSION_DENIED"

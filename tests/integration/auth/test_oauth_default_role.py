"""Integration test for default-role attachment on first OAuth sign-in.

Drives ``_upsert_user`` (the private helper that backs the OAuth
callback) directly against a real Postgres session so the M2M
``user_roles`` insert is exercised end-to-end. Covers three cases:

* Brand-new user → exactly the ``is_default=True`` role is attached.
* Existing user re-logging in → roles untouched (no auto re-attach).
* No default role configured → user is created without roles and the
  warning fires.

Each test runs in its own SAVEPOINT and rolls back on teardown so the
shared Postgres state stays clean.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

asyncpg = pytest.importorskip("asyncpg")

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402

from src.auth.oauth_google import _upsert_user  # noqa: E402
from src.model.auth import Role, User  # noqa: E402
from src.repository.auth import UserRepository  # noqa: E402


@pytest.fixture
async def session(pg_engine) -> AsyncIterator[AsyncSession]:
    """Yield a session whose work rolls back on teardown.

    Wraps the body in an outer transaction so every assertion runs
    against committed-looking state without polluting the shared DB.
    """
    maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with maker() as s:
        await s.begin()
        try:
            yield s
        finally:
            await s.rollback()


def _claims(email: str = "newbie@example.com") -> dict:
    """Build a minimal verified-email claims dict accepted by the upsert."""
    return {
        "email": email,
        "email_verified": True,
        "given_name": "New",
        "family_name": "Bie",
    }


@pytest.mark.asyncio
async def test_first_signin_attaches_default_role(session: AsyncSession) -> None:
    """A brand-new OAuth user comes back wearing the default role."""
    default_role = Role(name="oauth_default", is_default=True)
    other_role = Role(name="admin", is_default=False)
    session.add_all([default_role, other_role])
    await session.flush()

    user = await _upsert_user(session, _claims())

    fetched = await UserRepository(session).get_by_email(user.email)
    assert fetched is not None
    role_names = sorted(r.name for r in fetched.roles)
    assert role_names == ["oauth_default"]


@pytest.mark.asyncio
async def test_returning_user_keeps_existing_roles(session: AsyncSession) -> None:
    """Re-login does not auto-attach the default role to an existing user."""
    default_role = Role(name="oauth_default", is_default=True)
    session.add(default_role)
    existing = User(email="existing@example.com", first_name="Old", last_name="Hand")
    session.add(existing)
    await session.flush()

    user = await _upsert_user(session, _claims("existing@example.com"))

    fetched = await UserRepository(session).get_by_email(user.email)
    assert fetched is not None
    assert [r.name for r in fetched.roles] == []


@pytest.mark.asyncio
async def test_missing_default_role_logs_warning(
    session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No ``is_default`` row → user is created with empty roles and warning fires."""
    session.add(Role(name="not_default", is_default=False))
    await session.flush()

    with caplog.at_level("WARNING"):
        user = await _upsert_user(session, _claims("nodefault@example.com"))

    fetched = await UserRepository(session).get_by_email(user.email)
    assert fetched is not None
    assert fetched.roles == []
    assert any("no Role.is_default configured" in rec.message for rec in caplog.records)

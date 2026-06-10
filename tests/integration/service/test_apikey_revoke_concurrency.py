"""Concurrent-revoke serialisation test for ``APIKeyService.revoke``.

Two coroutines call ``revoke`` on the same active key with their own
sessions. With the ``FOR UPDATE`` row lock the second coroutine blocks
until the first commits, re-reads the now-revoked row, and reports
``already_revoked=True``. Without the lock both would observe
``is_revoked=False`` and both return ``(True, False)`` — the regression
this test guards against.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

asyncpg = pytest.importorskip("asyncpg")

from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker,
)

from src.model.auth import APIKey, User  # noqa: E402
from src.service.auth import APIKeyService  # noqa: E402


@pytest.fixture
async def sessionmaker(pg_engine) -> AsyncIterator[async_sessionmaker]:
    """Yield an ``async_sessionmaker`` bound to the integration engine."""
    yield async_sessionmaker(pg_engine, expire_on_commit=False)


async def _seed_key(maker: async_sessionmaker) -> tuple[int, int]:
    """Insert an owner + one active APIKey; return ``(api_key_id, user_id)``."""
    async with maker() as s:
        async with s.begin():
            owner = User(email="owner-revoke@example.com")
            s.add(owner)
            await s.flush()
            key = APIKey(
                user_id=owner.id,
                name="ci",
                prefix="abcd1234",
                secret="raw-secret-value",
                is_active=True,
            )
            s.add(key)
            await s.flush()
            return key.id, owner.id


async def _delete_key(maker: async_sessionmaker, user_id: int) -> None:
    """Roll back the test fixture rows."""
    async with maker() as s:
        async with s.begin():
            user = await s.get(User, user_id)
            if user is not None:
                await s.delete(user)


async def _revoke_in_own_session(
    maker: async_sessionmaker, api_key_id: int, user_id: int
) -> tuple[bool, bool]:
    """Open a fresh session, revoke, commit, return the tuple."""
    async with maker() as s:
        async with s.begin():
            user = await s.get(User, user_id)
            service = APIKeyService(s)
            return await service.revoke(api_key_id=api_key_id, user=user)


@pytest.mark.asyncio
async def test_concurrent_revoke_produces_one_winner(
    sessionmaker: async_sessionmaker,
) -> None:
    """Exactly one of two parallel revokes stamps the row; the other is idempotent."""
    api_key_id, user_id = await _seed_key(sessionmaker)
    try:
        results = await asyncio.gather(
            _revoke_in_own_session(sessionmaker, api_key_id, user_id),
            _revoke_in_own_session(sessionmaker, api_key_id, user_id),
        )
        assert sorted(results) == sorted([(True, False), (False, True)])
    finally:
        await _delete_key(sessionmaker, user_id)

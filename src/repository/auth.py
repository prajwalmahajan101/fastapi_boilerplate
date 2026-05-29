"""Async repositories for the auth domain.

Thin wrappers around the base async repository — the auth flows do
not need much beyond ``select`` / ``add`` / ``flush``. Kept as a
named module so service code can ``from src.repository.auth import
APIKeyRepository`` without reaching at ``BaseRepository[APIKey]`` in
every callsite.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base.repository import BaseRepository
from src.model.auth import APIKey, User


class UserRepository(BaseRepository[User]):
    """Async repository for the ``User`` model."""

    model = User

    async def get_by_email(self, email: str) -> User | None:
        """Fetch the user whose email matches (active or not).

        Args:
            email: Exact-match email.

        Returns:
            The user row or ``None`` if no match.
        """
        stmt = select(User).where(User.email == email).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


class APIKeyRepository(BaseRepository[APIKey]):
    """Async repository for the ``APIKey`` model."""

    model = APIKey

    async def list_for_user(self, user_id: int) -> list[APIKey]:
        """Return every (active) API key owned by ``user_id``.

        Soft-revoked keys are *included* so the operator can audit
        them; the auth dependency itself ignores revoked rows.
        """
        stmt = (
            select(APIKey)
            .where(APIKey.user_id == user_id, APIKey.is_active.is_(True))
            .order_by(APIKey.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_active_by_prefix(self, prefix: str) -> APIKey | None:
        """Return the active, non-revoked key matching ``prefix``."""
        stmt = (
            select(APIKey)
            .where(
                APIKey.prefix == prefix,
                APIKey.is_active.is_(True),
                APIKey.revoked_at.is_(None),
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


__all__ = ["APIKeyRepository", "UserRepository"]


def now_utc() -> datetime:
    """UTC ``datetime`` with explicit tz; reused by the service revoke path."""
    return datetime.now(timezone.utc)

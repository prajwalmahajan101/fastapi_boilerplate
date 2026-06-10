"""Business logic for the auth surface.

* :class:`APIKeyService` — issue + soft-revoke API keys; the create
  path returns the raw key once and never persists it in plaintext.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.api_key import generate_api_key
from src.core.base.service import BaseService
from src.repository.auth import APIKeyRepository, now_utc
from src.model.auth import APIKey

logger = logging.getLogger(__name__)


class APIKeyService(BaseService[APIKey]):
    """Service for API-key lifecycle operations."""

    model = APIKey
    repository_cls = APIKeyRepository

    def __init__(self, session: AsyncSession) -> None:
        """Bind the session + concrete repository.

        Args:
            session: Request-scoped async session.
        """
        super().__init__(session)
        # Keep a typed alias so this module reads like Django's port.
        self.repository: APIKeyRepository = self.repository  # type: ignore[assignment]

    async def create_for_user(self, *, user: Any, name: str) -> tuple[APIKey, str]:
        """Issue a fresh API key for ``user``.

        Returns ``(api_key_instance, raw_key)`` — ``raw_key`` is the
        plaintext token the caller must surface to the client *once*;
        the server stores only the encrypted form.

        Args:
            user: The owning ``User`` ORM row.
            name: Human-readable label (e.g. ``"CI pipeline"``).

        Returns:
            ``(api_key, raw_key)``.
        """
        raw_key, prefix = generate_api_key()
        api_key = APIKey(
            user_id=user.id,
            name=name,
            prefix=prefix,
            secret=raw_key,
            is_active=True,
        )
        self.session.add(api_key)
        await self.session.flush()
        await self.session.refresh(api_key)
        return api_key, raw_key

    async def revoke(self, *, api_key_id: int, user: Any) -> tuple[bool, bool]:
        """Soft-revoke an active key owned by ``user``.

        Takes a ``FOR UPDATE`` row lock on the API-key row so two
        concurrent revoke requests serialise — the loser blocks until
        the winner commits, then re-reads the now-revoked row and
        returns ``(False, True)`` instead of racing both into
        ``(True, False)``. Idempotent for the same reason: re-revoking
        is observed as ``already_revoked=True``.

        Lookup is scoped to ``user.id`` so a caller cannot revoke a
        key they do not own.

        Args:
            api_key_id: Primary key of the row to revoke.
            user: The owning ``User`` (used to scope the lookup).

        Returns:
            ``(revoked_now, already_revoked)``:
                * ``(True,  False)`` — this call stamped ``revoked_at``.
                * ``(False, True)``  — the key was already revoked.
                * ``(False, False)`` — no active key with that id for
                  this user; the caller should return 404.
        """
        api_key = await self.repository.get_by_id_for_update(api_key_id)
        if api_key is None or api_key.user_id != user.id:
            return (False, False)
        if api_key.is_revoked:
            return (False, True)
        api_key.revoked_at = now_utc()
        await self.session.flush()
        return (True, False)


__all__ = ["APIKeyService"]

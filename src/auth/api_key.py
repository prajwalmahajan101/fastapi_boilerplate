"""X-API-Key authentication provider.

The flow:

1. Read ``X-API-Key`` from the incoming request.
2. Look up the active, non-revoked row matching the 8-char prefix.
3. Compare the supplied key against the decrypted ``secret`` in
   constant time.
4. Confirm the owning user is active.
5. Debounce a ``last_used_at`` update via the rate-limit cache
   (matches the Django auth backend behaviour) so a high-RPS key does
   not generate one UPDATE per request.

On failure the provider raises :class:`AuthenticationFailedError` —
the exception handler renders 401 with the standard envelope. The
exception family is registered with the handler in
``src/core/exceptions/handlers.py`` so routes do not need explicit
``responses=`` entries.

The provider self-registers under the name ``"api_key"`` at import
time; the composite dependency lives in :mod:`src.auth.registry`.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.base import AuthResult
from src.core.exceptions.auth import (
    APIKeyRevokedError,
    AuthenticationFailedError,
)
from src.core.utils.fire_and_forget import FireAndForgetQueue, register

if TYPE_CHECKING:
    from fastapi import Request

    from src.model.auth import APIKey

logger = logging.getLogger(__name__)

_LAST_USED_DEBOUNCE_SECONDS = 300
_LAST_USED_TIMEOUT_SECONDS = 5.0

# Background queue for ``last_used_at`` UPDATEs. Keeping the write off
# the auth dependency keeps the read path read-only (ISSUE-026) and
# ``drain_all()`` at shutdown waits for in-flight writes.
_last_used_queue = register(
    FireAndForgetQueue(max_pending=500, name="api_key_last_used")
)


def generate_api_key() -> tuple[str, str]:
    """Mint a fresh URL-safe token and return ``(raw_key, prefix)``.

    The caller (typically :class:`APIKeyService`) stores ``raw_key`` in
    the encrypted ``secret`` column and ``prefix`` as the lookup
    column. The raw key is returned to the caller exactly once and is
    never recoverable thereafter.

    Returns:
        ``(raw_key, prefix)`` — both strings, ``prefix`` is the first
        eight characters of ``raw_key``.
    """
    raw_key = secrets.token_urlsafe(32)
    return raw_key, raw_key[:8]


async def _load_api_key_by_prefix(
    session: AsyncSession, prefix: str
) -> "APIKey | None":
    """Resolve the single active, non-revoked APIKey row for ``prefix``.

    Uses the partial unique index defined on the model so Postgres
    serves the lookup from the index alone.
    """
    from src.model.auth import APIKey  # noqa: PLC0415

    stmt = (
        select(APIKey)
        .where(
            APIKey.prefix == prefix,
            APIKey.is_active.is_(True),
            APIKey.revoked_at.is_(None),
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _debounce_last_used(api_key_id: int) -> bool:
    """Return ``True`` when this caller should update ``last_used_at``."""
    from src.core.resilience.cache.provider import get_cache  # noqa: PLC0415

    cache_key = f"apikey_used_{api_key_id}"
    try:
        cache = await get_cache("default")
        if await cache.add(cache_key, "1", ttl=_LAST_USED_DEBOUNCE_SECONDS):
            return True
        return False
    except Exception:  # noqa: BLE001
        # On cache failure prefer the conservative path — the next
        # request still gets one write attempt within the debounce
        # window. Worst case: an extra UPDATE.
        return True


async def _persist_last_used(api_key_id: int, ts: datetime) -> None:
    """Update ``APIKey.last_used_at`` in a fresh short-lived session.

    Runs from :data:`_last_used_queue`. Opens its own session because
    the originating request's session is gone by the time this awaits;
    bounds the work with a 5-second timeout so a stalled write cannot
    hold the queue. All errors are logged and swallowed — the audit
    log is the source of truth for "this key was used", so a missed
    timestamp is acceptable.

    Args:
        api_key_id: Primary key of the row to update.
        ts: Timestamp to stamp (typically ``datetime.now(timezone.utc)``
            captured at the originating request).
    """
    import asyncio  # noqa: PLC0415

    from sqlalchemy import update  # noqa: PLC0415

    from src.core.utils.db import get_app_engine, get_sessionmaker  # noqa: PLC0415
    from src.model.auth import APIKey  # noqa: PLC0415

    try:
        engine = await get_app_engine()
        maker = get_sessionmaker(engine)
        async with asyncio.timeout(_LAST_USED_TIMEOUT_SECONDS):
            async with maker() as bg_session:
                async with bg_session.begin():
                    await bg_session.execute(
                        update(APIKey)
                        .where(APIKey.id == api_key_id)
                        .values(last_used_at=ts)
                    )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "APIKey last_used_at update failed for id=%s: %s",
            api_key_id,
            exc,
        )


class APIKeyProvider:
    """X-API-Key :class:`AuthProvider` implementation."""

    name = "api_key"

    async def authenticate(
        self, request: "Request", session: AsyncSession
    ) -> AuthResult | None:
        """Validate ``X-API-Key`` and return an :class:`AuthResult`.

        Returns ``None`` when the header is absent so the registry can
        fall through to the next enabled provider.

        Args:
            request: Incoming Starlette request.
            session: Request-scoped session.

        Returns:
            An :class:`AuthResult` carrying the user + the owning
            ``APIKey`` row, or ``None`` when no header is present.

        Raises:
            AuthenticationFailedError: Header present but invalid.
            APIKeyRevokedError: Key recognised but revoked.
        """
        raw_key = request.headers.get("x-api-key")
        if not raw_key:
            return None
        if len(raw_key) < 8:
            raise AuthenticationFailedError("Invalid API key.")

        prefix = raw_key[:8]
        api_key = await _load_api_key_by_prefix(session, prefix)
        if api_key is None:
            raise AuthenticationFailedError("Invalid API key.")

        if not secrets.compare_digest(api_key.secret, raw_key):
            raise AuthenticationFailedError("Invalid API key.")

        if api_key.is_revoked:
            # Defence in depth — the lookup already filters by
            # ``revoked_at IS NULL`` but a race between revoke + auth
            # could theoretically observe a recently-revoked key.
            raise APIKeyRevokedError()

        user = api_key.user
        if user is None or not user.is_active:
            raise AuthenticationFailedError("User account is disabled.")

        if await _debounce_last_used(api_key.id):
            # Fire-and-forget: the auth dependency stays read-only and a
            # stalled write cannot stall the request. last_used_at is
            # observability metadata, not authoritative state — the
            # audit log records every authed call regardless.
            _last_used_queue.submit(
                _persist_last_used(api_key.id, datetime.now(timezone.utc))
            )

        # Stash on request.state so handlers / RBAC checks can pull the
        # owning key out without re-running the lookup. (The registry
        # additionally stashes the full AuthResult under request.state.auth.)
        request.state.api_key = api_key
        return AuthResult(user=user, provider=self.name, principal=api_key)


# Self-register at import time so the registry picks us up.
from src.auth import registry as _registry  # noqa: E402

_registry.register(APIKeyProvider())


__all__ = ["APIKeyProvider", "generate_api_key"]

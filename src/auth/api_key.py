"""X-API-Key authentication dependency.

The flow:

1. Read ``X-API-Key`` from the incoming request.
2. Look up the active, non-revoked row matching the 8-char prefix.
3. Compare the supplied key against the decrypted ``secret`` in
   constant time.
4. Confirm the owning user is active.
5. Debounce a ``last_used_at`` update via the rate-limit cache
   (matches the Django auth backend behaviour) so a high-RPS key does
   not generate one UPDATE per request.

On failure the dependency raises :class:`AuthenticationFailedError` —
the exception handler renders 401 with the standard envelope. The
exception family is registered with the handler in
``src/core/exceptions/handlers.py`` so routes do not need explicit
``responses=`` entries.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db.dependencies import get_session
from src.core.exceptions.auth import (
    APIKeyRevokedError,
    AuthenticationFailedError,
)

if TYPE_CHECKING:
    from src.model.auth import APIKey, User

logger = logging.getLogger(__name__)

_LAST_USED_DEBOUNCE_SECONDS = 300


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
    """Return ``True`` when this caller should update ``last_used_at``.

    Uses ``rate_limit`` cache alias (via ``get_cache``) so write
    debounce is shared across workers. Failure to reach Redis falls
    back to in-memory which still bounds the write rate per worker.
    """
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


async def current_user_optional(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> "User | None":
    """Resolve the authenticated user, or ``None`` when the header is absent.

    Use this on endpoints that work for both anonymous and authenticated
    callers (e.g. a public health endpoint that varies its response when
    a key is present).

    Args:
        request: Incoming Starlette request.
        session: Request-scoped session injected via :func:`get_session`.

    Returns:
        The ``User`` row when authentication succeeds; ``None`` when
        the header is absent.

    Raises:
        AuthenticationFailedError: When the header is present but the
            key fails the prefix lookup or constant-time compare.
        APIKeyRevokedError: When the key exists but has been revoked.
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
        api_key.last_used_at = datetime.now(timezone.utc)
        await session.flush()

    # Stash on request.state so handlers / RBAC checks can pull the
    # owning key out without re-running the lookup.
    request.state.api_key = api_key
    return user


async def current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> "User":
    """Mandatory variant of :func:`current_user_optional`.

    Raises:
        AuthenticationFailedError: When the header is absent or the
            credentials fail to validate.
    """
    user = await current_user_optional(request, session)
    if user is None:
        raise AuthenticationFailedError("Missing API key.")
    return user


__all__ = ["current_user", "current_user_optional", "generate_api_key"]

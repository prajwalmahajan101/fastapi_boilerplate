"""Pluggable auth provider registry + composite ``current_user`` dep.

Each provider self-registers via :func:`register`; the order routes see
is the order of ``settings.auth_enabled_providers``. A deployment that
sets

    AUTH_ENABLED_PROVIDERS='["jwt","api_key"]'

gets JWT consulted first, then the X-API-Key path. A deployment that
sets only ``["api_key"]`` runs identically to the pre-pluggable code.

Unknown names in ``auth_enabled_providers`` are skipped (and logged at
WARNING once per process), so a typo never silently disables auth on
the routes that depend on :func:`current_user`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.base import AuthProvider, AuthResult
from src.core.db.dependencies import get_session
from src.core.exceptions.auth import AuthenticationFailedError
from src.core.runtime import get_settings

if TYPE_CHECKING:
    from src.model.auth import User

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, AuthProvider] = {}
_WARNED_UNKNOWN: set[str] = set()


def register(provider: AuthProvider) -> None:
    """Register ``provider`` under its ``name``.

    Idempotent — re-registering a provider with the same name replaces
    the previous instance. Used by each provider module at import time.

    Args:
        provider: A concrete implementer of :class:`AuthProvider`.
    """
    _REGISTRY[provider.name] = provider


def unregister(name: str) -> None:
    """Drop ``name`` from the registry — primarily a test helper.

    Args:
        name: The provider name to remove. No-op when absent.
    """
    _REGISTRY.pop(name, None)


def registered_names() -> list[str]:
    """Return the names of every currently-registered provider."""
    return list(_REGISTRY)


def enabled_providers() -> list[AuthProvider]:
    """Return the active providers in the order routes will consult them.

    Reads ``settings.auth_enabled_providers``. Unknown names are
    skipped with a one-shot warning so a typo cannot silently disable
    authentication.
    """
    settings = get_settings()
    out: list[AuthProvider] = []
    for name in getattr(settings, "auth_enabled_providers", ["api_key"]):
        provider = _REGISTRY.get(name)
        if provider is None:
            if name not in _WARNED_UNKNOWN:
                logger.warning(
                    "auth_enabled_providers references unknown provider %r — "
                    "registered names: %s",
                    name,
                    sorted(_REGISTRY),
                )
                _WARNED_UNKNOWN.add(name)
            continue
        out.append(provider)
    return out


async def _resolve(request: Request, session: AsyncSession) -> AuthResult | None:
    """Walk the enabled providers and return the first successful result."""
    for provider in enabled_providers():
        result = await provider.authenticate(request, session)
        if result is not None:
            request.state.auth = result
            return result
    return None


async def current_user_optional(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> "User | None":
    """Resolve the authenticated user, or ``None`` when no credentials present.

    Use this on endpoints that work for both anonymous and authenticated
    callers.

    Args:
        request: Incoming Starlette request.
        session: Request-scoped session injected via :func:`get_session`.

    Returns:
        The authenticated ``User`` row or ``None`` when no enabled
        provider saw credentials of its kind.

    Raises:
        AuthenticationFailedError: When a provider saw credentials of
            its kind but they failed to validate.
    """
    result = await _resolve(request, session)
    return result.user if result is not None else None


async def current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> "User":
    """Mandatory variant of :func:`current_user_optional`.

    Raises:
        AuthenticationFailedError: When no enabled provider could
            authenticate the request.
    """
    result = await _resolve(request, session)
    if result is None:
        raise AuthenticationFailedError("Missing credentials.")
    return result.user


__all__ = [
    "current_user",
    "current_user_optional",
    "enabled_providers",
    "register",
    "registered_names",
    "unregister",
]

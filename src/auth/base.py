"""Auth provider contract — shared by api_key / jwt / oauth_google.

Every concrete provider implements :class:`AuthProvider` (a
``Protocol``) by exposing:

* ``name`` — short identifier matching the ordered list in
  ``settings.auth_enabled_providers``;
* ``authenticate(request, session)`` — return :class:`AuthResult` when
  the inbound credentials resolve to a user, ``None`` when this
  provider sees no credentials of its kind, or raise an
  :class:`AuthenticationFailedError` subclass when credentials are
  present but invalid.

The registry in :mod:`src.auth.registry` iterates the enabled
providers in order; the first ``AuthResult`` wins. Returning ``None``
(rather than raising) is what lets the registry fall through to the
next provider when, e.g., the ``X-API-Key`` header is absent but a
``Bearer`` token is present.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.model.auth import APIKey, User


@dataclass(slots=True)
class AuthResult:
    """Outcome of a successful authentication.

    Attributes:
        user: The authenticated ``User`` ORM row, with eager-loaded
            ``roles.permissions`` so the RBAC layer can decide without
            a second round-trip.
        provider: The :attr:`AuthProvider.name` of the producing
            provider — handy for audit / metrics tagging.
        principal: For ``api_key``, the owning :class:`APIKey` row;
            ``None`` for every other provider.
        token_claims: For ``jwt`` / ``oauth_google``, the decoded JWT
            claim dict; ``None`` for ``api_key``.
    """

    user: "User"
    provider: str
    principal: "APIKey | None" = None
    token_claims: dict[str, Any] | None = field(default=None)


@runtime_checkable
class AuthProvider(Protocol):
    """Authenticate inbound requests.

    Implementations are stateless and shared across requests — keep
    expensive setup (HTTP clients, JWKS caches) on module-level
    singletons resolved lazily on the first call.
    """

    name: str

    async def authenticate(
        self, request: "Request", session: "AsyncSession"
    ) -> AuthResult | None:
        """Resolve ``request`` to an :class:`AuthResult`.

        Args:
            request: The inbound Starlette / FastAPI request.
            session: Request-scoped async session.

        Returns:
            An :class:`AuthResult` on success, or ``None`` when this
            provider sees no credentials and the registry should fall
            through to the next one.

        Raises:
            AuthenticationFailedError: When credentials of this
                provider's kind are present but invalid.
        """
        ...


__all__ = ["AuthProvider", "AuthResult"]
